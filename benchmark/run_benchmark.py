"""Compare Hugging Face models on the PrescribeX audit pipeline (accuracy + time).

The script does not change the pipeline. It imports ``audit_pipeline`` as-is and replays every prescription the way the
React writer does: one live section audit per section, in writer order
(chiefComplaints -> pastHistory -> investigations -> diagnosis (+ investigations recheck) -> rx -> advice),
each call seeing only the fields entered so far, then the final "Run AI Audit" with the live results.

Every model runs in its own subprocess (clean GPU memory; one crash or OOM does not stop the rest), loaded
in-process by ``LocalChatProvider`` with ``HF_QUANT=4bit`` (bitsandbytes).

Run from the project root (the folder that holds ``audit_pipeline``)::

    .venv\\Scripts\\python.exe benchmark\\run_benchmark.py                          # models marked "run" in models.json
    .venv\\Scripts\\python.exe benchmark\\run_benchmark.py --models Qwen/Qwen2.5-7B-Instruct microsoft/phi-4
    .venv\\Scripts\\python.exe benchmark\\run_benchmark.py --models rules-only      # no LLM: rule engine baseline / dry run
    .venv\\Scripts\\python.exe benchmark\\run_benchmark.py --cases my_rx.json --limit 5 --no-live

Input (``--cases``): a JSON list, or ``{"cases": [...]}``. Each case is either
``{"case_id", "patient", "visit", "history", "expected_errors"}`` (see synthetic_prescriptions_all.json) or any file
shape the app saves (finalPrescription step file, downloaded prescription). ``expected_errors`` is optional;
without it a case is timed but not scored.

Output: ``benchmark/results/<timestamp>/`` with one ``<model>.json`` per model (every output, finding and timing)
and ``summary.json`` + ``summary.csv`` (one row per model) for the presentation.
"""

from __future__ import annotations

import argparse
import csv
import gc
import json
import os
import re
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parent.parent
BENCH = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Writer order and the fields each section adds (prescribex-app/src/App.jsx SECTION_ORDER, SECTION_VISIT_FIELDS).
# Examination data (vitals, ANC notes, results) is entered before the first audited section.
SECTION_STEPS = (
    ("chiefComplaints", ("chiefComplaints",)),
    ("pastHistory", ("pastHistory",)),
    ("investigations", ("investigations",)),
    ("diagnosis", ("diagnosis",)),
    ("rx", ("rxList",)),
    ("advice", ("adviceList", "followUps")),
)
SECTION_FIELDS = {field for _, fields in SECTION_STEPS for field in fields}
RECHECK_ON_SAVE = {"diagnosis": ("investigations",)}  # App.jsx LIVE_AUDIT_RECHECK_ON_SAVE
SIGNIFICANT_LEVELS = {"moderate", "major", "critical"}
RULES_ONLY = "rules-only"


# --------------------------------------------------------------------------- cases
def load_cases(path: Path, limit: int | None) -> list[dict[str, Any]]:
    from audit_pipeline.app_adapter import normalize_payload

    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    items = raw.get("cases", [raw]) if isinstance(raw, Mapping) else raw
    cases = []
    for index, item in enumerate(items, 1):
        if isinstance(item.get("visit"), Mapping):
            case = dict(item)
        else:  # a file saved by the app: let the pipeline's own adapter read it
            norm = normalize_payload(item)
            case = {"patient": norm["patient"], "visit": dict(norm["record"], id=norm["visit_id"]),
                    "history": norm["history"], "expected_errors": item.get("expected_errors")}
        case.setdefault("case_id", f"CASE-{index:03d}")
        case.setdefault("history", [])
        cases.append(case)
    return cases[:limit] if limit else cases


def visit_after(visit: Mapping[str, Any], step_index: int) -> dict[str, Any]:
    """The visit as the writer holds it once steps 0..step_index are saved (later sections still empty)."""
    entered = {field for _, fields in SECTION_STEPS[: step_index + 1] for field in fields}
    return {key: value for key, value in visit.items() if key not in SECTION_FIELDS or key in entered}


# --------------------------------------------------------------------------- providers
class RulesOnlyProvider:
    """No LLM: confirms every rule-engine candidate. Baseline for what the model adds, and a dry run."""

    model = RULES_ONLY

    def __init__(self) -> None:
        self.last_output_character_count = 0
        self.last_response_text_preview = ""

    def audit(self, *, system: str, prompt: str, max_tokens: int) -> dict[str, Any]:
        from audit_pipeline.gemma_provider import UnsupportedPrompt

        if "TASK_JSON=" not in prompt:
            raise UnsupportedPrompt("rules-only baseline does not answer router/classifier prompts")
        task = json.loads(prompt.split("TASK_JSON=", 1)[1])
        # A fixed reason: audit_writer drops findings whose "why" only echoes the candidate hypothesis.
        findings = [
            {"candidate_id": c.get("candidate_id", ""), "rule_name": c.get("rule_name", ""),
             "rule_id": c.get("rule_id", ""), "correction": "",
             "why": "Accepted by the rules-only baseline without any language model review of this candidate.",
             "evidence": c.get("evidence", ""),
             "confidence": "high" if c.get("evidence_strength") == "high" else "medium"}
            for c in task.get("candidates", []) or [] if isinstance(c, Mapping)
        ]
        return {"section": task.get("section", ""), "findings": findings}


class TimedProvider:
    """Wraps the pipeline's provider and records every model call (time, output tokens, JSON failures)."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.model = getattr(inner, "model", "")
        self.calls: list[dict[str, Any]] = []
        self._tokens = 0
        generate = getattr(inner, "_generate", None)
        if callable(generate) and getattr(inner, "_tokenizer", None) is not None:
            def counted(*args: Any, **kwargs: Any) -> str:
                text = generate(*args, **kwargs)
                self._tokens += len(inner._tokenizer(text, add_special_tokens=False)["input_ids"])
                return text
            inner._generate = counted

    def __getattr__(self, name: str) -> Any:  # last_output_character_count etc. used by audit_writer
        return getattr(self.inner, name)

    def audit(self, *, system: str, prompt: str, max_tokens: int) -> Any:
        self._tokens = 0
        kind = "adjudication" if "TASK_JSON=" in prompt else "router/classifier"
        section = re.search(r'"section"\s*:\s*"([A-Za-z]+)"', prompt)
        started = perf_counter()
        error = ""
        try:
            return self.inner.audit(system=system, prompt=prompt, max_tokens=max_tokens)
        except Exception as exc:
            error = f"{type(exc).__name__}: {str(exc)[:200]}"
            raise
        finally:
            self.calls.append({
                "kind": kind, "section": section.group(1) if section else "",
                "seconds": round(perf_counter() - started, 3), "output_tokens": self._tokens,
                "output_chars": getattr(self.inner, "last_output_character_count", 0),
                "error": error,
                "unsupported": error.startswith("UnsupportedPrompt"),
            })

    def take_calls(self) -> list[dict[str, Any]]:
        calls, self.calls = self.calls, []
        return calls


def fold_system_if_needed(provider: Any) -> bool:
    """Some chat templates (Gemma 2...) reject a system turn with an error the pipeline does not catch.
    For those, render system + user as one user turn so the model can still be compared."""
    tokenizer = getattr(provider, "_tokenizer", None)
    if tokenizer is None or not getattr(tokenizer, "chat_template", None):
        return False
    try:
        tokenizer.apply_chat_template([{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
                                      tokenize=False, add_generation_prompt=True)
        return False
    except Exception:
        original = provider._generate

        def folded(system: str, prompt: str, max_tokens: int, prefix: str) -> str:
            return original("", system + "\n\n" + prompt, max_tokens, prefix)

        provider._generate = folded
        return True


def prequantized(model: str) -> str:
    """Quantization method of a repo that is already quantized on the Hub (bnb-4bit, GPTQ, AWQ...), else ''."""
    from transformers import AutoConfig

    config = AutoConfig.from_pretrained(model, token=(os.getenv("HF_TOKEN") or "").strip() or None)
    quant = getattr(config, "quantization_config", None)
    if quant is None:
        return ""
    method = quant.get("quant_method") if isinstance(quant, Mapping) else getattr(quant, "quant_method", "")
    return str(getattr(method, "value", method) or "quantized")


def build_provider(model: str) -> tuple[Any, dict[str, Any]]:
    if model == RULES_ONLY:
        return RulesOnlyProvider(), {}
    # A repo that is already 4-bit loads as published; a full-precision repo is quantized to 4-bit at load.
    method = prequantized(model)
    os.environ.update({"AUDIT_PROVIDER": "local", "HF_MODEL": model, "HF_QUANT": "none" if method else "4bit",
                       "HF_ADAPTER": ""})
    for name in ("LLM_BASE_URL", "LLM_MODEL"):  # always load in-process
        os.environ.pop(name, None)
    import torch
    from audit_pipeline.provider import LocalChatProvider

    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()
    started = perf_counter()
    provider = LocalChatProvider()
    info = {"load_seconds": round(perf_counter() - started, 2),
            "quantization": f"pre-quantized on the Hub ({method})" if method else "4bit (bitsandbytes nf4, at load)",
            "system_role_folded": fold_system_if_needed(provider),
            "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu",
            "vram_after_load_gb": round(torch.cuda.memory_allocated() / 2**30, 2) if torch.cuda.is_available() else 0}
    return provider, info


# --------------------------------------------------------------------------- scoring
def all_findings(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    grouped = report.get("errors_by_section", {}) or {}
    return [dict(item) for items in grouped.values() for item in (items or []) if isinstance(item, Mapping)]


def matches(finding: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
    """A finding catches an expected error when its rule is one of the expected rule-id prefixes and it is
    about the right thing (one of the keywords appears in its reason/evidence). The model can only confirm
    rule-engine candidates, so the rule id is always one of the engine's."""
    rule_id = str(finding.get("rule_id", "")).casefold()
    if not any(rule_id.startswith(str(prefix).casefold()) for prefix in expected.get("rule_ids", [])):
        return False
    keywords = expected.get("keywords", [])
    text = " ".join(str(finding.get(k, "")) for k in ("rule_name", "why", "evidence", "correction")).casefold()
    return not keywords or any(str(word).casefold() in text for word in keywords)


def score(findings: list[dict[str, Any]], expected: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    if expected is None:
        return None
    significant = [f for f in findings if str(f.get("concern_level", "moderate")).casefold() in SIGNIFICANT_LEVELS]
    detected = [e["id"] for e in expected if any(matches(f, e) for f in findings)]
    return {
        "expected": len(expected),
        "detected": len(detected),
        "detected_ids": detected,
        "missed_ids": [e["id"] for e in expected if e["id"] not in detected],
        "critical_expected": sum(1 for e in expected if e.get("severity") == "critical"),
        "critical_detected": sum(1 for e in expected if e.get("severity") == "critical" and e["id"] in detected),
        "findings": len(findings),
        "correct_findings": sum(1 for f in findings if any(matches(f, e) for e in expected)),
        "significant_findings": len(significant),
        "correct_significant_findings": sum(1 for f in significant if any(matches(f, e) for e in expected)),
    }


def ratio(numerator: float, denominator: float) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def aggregate(model: str, info: Mapping[str, Any], cases: list[dict[str, Any]]) -> dict[str, Any]:
    def total(key: str, mode: str = "final") -> int:
        return sum(((c.get("score") or {}).get(mode) or {}).get(key, 0) or 0 for c in cases)

    scored = [c for c in cases if c.get("score")]
    calls = [call for c in cases for call in c.get("llm_calls", [])]
    llm_calls = [call for call in calls if not call["unsupported"]]
    tokens = sum(call["output_tokens"] for call in llm_calls)
    llm_seconds = sum(call["seconds"] for call in llm_calls)
    case_seconds = [c["timing"]["total_seconds"] for c in cases if "timing" in c]
    live_seconds = [s["seconds"] for c in cases for s in c.get("live_sections", [])]
    precision = ratio(total("correct_findings"), total("findings"))
    recall = ratio(total("detected"), total("expected"))
    clean = [c for c in scored if c["expected_count"] == 0]
    row = {
        "model": model,
        "cases": len(cases),
        "failed_cases": sum(1 for c in cases if c.get("error")),
        "precision": precision,
        "recall": recall,
        "f1": round(2 * precision * recall / (precision + recall), 4) if precision and recall else None,
        "significant_precision": ratio(total("correct_significant_findings"), total("significant_findings")),
        "critical_recall": ratio(total("critical_detected"), total("critical_expected")),
        "live_recall": ratio(total("detected", "live"), total("expected", "live")),
        "clean_cases_with_false_alarm": sum(1 for c in clean if c["score"]["final"]["significant_findings"]),
        "clean_cases": len(clean),
        "avg_case_seconds": ratio(sum(case_seconds), len(case_seconds)),
        "avg_live_section_seconds": ratio(sum(live_seconds), len(live_seconds)),
        "avg_final_audit_seconds": ratio(sum(c["timing"]["final_seconds"] for c in cases if "timing" in c), len(case_seconds)),
        "total_llm_seconds": round(llm_seconds, 2),
        "llm_calls": len(llm_calls),
        "avg_llm_call_seconds": ratio(llm_seconds, len(llm_calls)),
        "output_tokens": tokens,
        "tokens_per_second": ratio(tokens, llm_seconds),
        "llm_errors": sum(1 for call in llm_calls if call["error"]),
        "llm_error_rate": ratio(sum(1 for call in llm_calls if call["error"]), len(llm_calls)),
        "load_seconds": info.get("load_seconds"),
        "peak_vram_gb": info.get("peak_vram_gb"),
    }
    by_category: dict[str, dict[str, int]] = {}
    for c in scored:
        bucket = by_category.setdefault(c.get("category") or "uncategorised", {"expected": 0, "detected": 0})
        bucket["expected"] += c["score"]["final"]["expected"]
        bucket["detected"] += c["score"]["final"]["detected"]
    row["recall_by_category"] = {k: ratio(v["detected"], v["expected"]) for k, v in sorted(by_category.items())}
    return row


# --------------------------------------------------------------------------- one model
def run_case(service: Any, timed: TimedProvider, case: Mapping[str, Any], live: bool) -> dict[str, Any]:
    patient, visit, history = case["patient"], case["visit"], case.get("history", [])
    result: dict[str, Any] = {"case_id": case["case_id"], "title": case.get("title", ""),
                              "category": case.get("category", ""),
                              "expected_count": len(case["expected_errors"]) if case.get("expected_errors") is not None else None,
                              "live_sections": [], "llm_calls": []}
    started = perf_counter()
    live_audits: dict[str, Any] = {}
    if live:
        def section_audit(section: str, step_index: int, recheck: bool = False) -> None:
            payload = {"section": section, "revision": 1, "patient": patient,
                       "visit": visit_after(visit, step_index), "history": history}
            t0 = perf_counter()
            try:
                out = service.audit_section(payload)["live_audit"]
                error = ""
            except Exception as exc:  # noqa: BLE001 - a model failure is a result, not a crash
                out, error = {"status": "error", "findings": []}, f"{type(exc).__name__}: {exc}"
            calls = timed.take_calls()
            if out.get("status") in ("clear", "moderate", "critical"):
                live_audits[section] = out
            result["live_sections"].append({
                "section": section, "recheck": recheck, "seconds": round(perf_counter() - t0, 3),
                "status": out.get("status"), "summary": out.get("summary", ""), "findings": out.get("findings", []),
                "warnings": out.get("warnings", []), "error": error,
                "llm_seconds": round(sum(c["seconds"] for c in calls if not c["unsupported"]), 3),
            })
            result["llm_calls"].extend(calls)

        for index, (section, _) in enumerate(SECTION_STEPS):
            section_audit(section, index)
            for dependent in RECHECK_ON_SAVE.get(section, ()):
                section_audit(dependent, index, recheck=True)

    t0 = perf_counter()
    final = service.audit({"patient": patient, "visit": visit, "history": history, "live_audits": live_audits})
    final_calls = timed.take_calls()
    result["llm_calls"].extend(final_calls)
    report = final["report"]
    result["final"] = {
        "status": report.get("status"),
        "findings": all_findings(report),
        "summary": report.get("consolidated_summary", []),
        "candidate_count": report.get("metadata", {}).get("candidate_count"),
        "warnings": report.get("metadata", {}).get("warnings", []),
        "rejected_high_evidence_candidates": report.get("metadata", {}).get("rejected_high_evidence_candidates", []),
        "live_audit_comparison": final.get("live_audit_comparison"),
        "pipeline_input": final.get("pipeline_input"),
    }
    result["timing"] = {
        "total_seconds": round(perf_counter() - started, 3),
        "live_seconds": round(sum(s["seconds"] for s in result["live_sections"]), 3),
        "final_seconds": round(perf_counter() - t0, 3),
        "llm_seconds": round(sum(c["seconds"] for c in result["llm_calls"] if not c["unsupported"]), 3),
        "llm_calls": sum(1 for c in result["llm_calls"] if not c["unsupported"]),
        "output_tokens": sum(c["output_tokens"] for c in result["llm_calls"]),
    }
    expected = case.get("expected_errors")
    if expected is not None:
        live_findings = [f for s in result["live_sections"] for f in s["findings"]]
        result["score"] = {"final": score(result["final"]["findings"], expected),
                           "live": score(live_findings, expected) if live else None}
    return result


def run_worker(model: str, cases_path: Path, out_path: Path, limit: int | None, live: bool) -> int:
    from audit_pipeline.app_server import AuditService, load_env

    os.environ["PRESCRIPTION_DATA_DIR"] = ""  # never mix real saved patients into the benchmark
    load_env()  # HF_TOKEN etc.; values set above win (load_dotenv override=False)
    cases = load_cases(cases_path, limit)
    print(f"[{model}] loading ({len(cases)} cases)...", flush=True)
    try:
        provider, info = build_provider(model)
    except Exception as exc:  # noqa: BLE001 - gated, missing, unsupported or OOM: record it and move on
        print(f"[{model}] could not load: {type(exc).__name__}: {str(exc)[:300]}", flush=True)
        info = {"load_error": f"{type(exc).__name__}: {str(exc)[:1000]}"}
        write_model_file(out_path, model, info, [], cases_path)
        return 1
    timed = TimedProvider(provider)
    service = AuditService(provider=timed, data_dir="")
    results = []
    for number, case in enumerate(cases, 1):
        try:
            item = run_case(service, timed, case, live)
        except Exception as exc:  # noqa: BLE001
            item = {"case_id": case["case_id"], "title": case.get("title", ""), "category": case.get("category", ""),
                    "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc(limit=5),
                    "llm_calls": timed.take_calls()}
        results.append(item)
        sc = (item.get("score") or {}).get("final") or {}
        print(f"[{model}] {number}/{len(cases)} {item['case_id']} "
              f"{item.get('timing', {}).get('total_seconds', '-')}s "
              f"detected {sc.get('detected', '-')}/{sc.get('expected', '-')}"
              f"{'  ERROR ' + item['error'] if item.get('error') else ''}", flush=True)
        # Save after every case so a crash keeps the finished ones.
        write_model_file(out_path, model, info, results, cases_path)
    try:
        import torch
        if torch.cuda.is_available():
            info["peak_vram_gb"] = round(torch.cuda.max_memory_allocated() / 2**30, 2)
    except ImportError:
        pass
    write_model_file(out_path, model, info, results, cases_path)
    return 0


def write_model_file(path: Path, model: str, info: Mapping[str, Any], results: list, cases_path: Path) -> None:
    path.write_text(json.dumps({
        "model": model, "quantization": "none" if model == RULES_ONLY else info.get("quantization", "4bit"),
        "cases_file": str(cases_path), "run_at": datetime.now().isoformat(timespec="seconds"),
        "load_info": info, "summary": aggregate(model, info, results), "cases": results,
    }, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


# --------------------------------------------------------------------------- driver
def slug(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "__", model)


def default_models() -> list[str]:
    config = json.loads((BENCH / "models.json").read_text(encoding="utf-8"))
    return [m["id"] for m in config["models"] if m.get("run")]


def model_in_cache(model: str) -> bool:
    try:
        from huggingface_hub import scan_cache_dir
        return any(repo.repo_id == model for repo in scan_cache_dir().repos)
    except Exception:  # noqa: BLE001
        return True  # unknown -> never delete


def delete_from_cache(model: str) -> None:
    from huggingface_hub import scan_cache_dir

    cache = scan_cache_dir()
    revisions = [rev.commit_hash for repo in cache.repos if repo.repo_id == model for rev in repo.revisions]
    if revisions:
        strategy = cache.delete_revisions(*revisions)
        print(f"  removed {model} from the HF cache ({strategy.expected_freed_size_str})")
        strategy.execute()


def write_summary(run_dir: Path) -> None:
    rows = []
    for file in sorted(run_dir.glob("*.json")):
        if file.name == "summary.json":
            continue
        data = json.loads(file.read_text(encoding="utf-8"))
        rows.append(dict(data["summary"], quantization=data["quantization"], gpu=data["load_info"].get("gpu", ""),
                         load_error=data["load_info"].get("load_error", "")))
    rows.sort(key=lambda r: (-(r.get("f1") or 0), r.get("avg_case_seconds") or 1e9))
    (run_dir / "summary.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    columns = [k for k in rows[0] if k != "recall_by_category"] if rows else []
    with (run_dir / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"\n{'model':45} {'P':>6} {'R':>6} {'F1':>6} {'critR':>6} {'s/case':>7} {'tok/s':>6} {'errs':>5}")
    for r in rows:
        fmt = lambda v: "-" if v is None else f"{v:.2f}" if isinstance(v, float) else str(v)  # noqa: E731
        print(f"{r['model'][:45]:45} {fmt(r['precision']):>6} {fmt(r['recall']):>6} {fmt(r['f1']):>6} "
              f"{fmt(r['critical_recall']):>6} {fmt(r['avg_case_seconds']):>7} {fmt(r['tokens_per_second']):>6} "
              f"{fmt(r['llm_errors']):>5}")
    print(f"\nSaved: {run_dir / 'summary.csv'}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--models", nargs="+", help=f"HF model ids (default: models.json entries with run=true); '{RULES_ONLY}' = no LLM")
    parser.add_argument("--cases", default=str(BENCH / "test_prescriptions.json"),
                        help="default: the 10-case set; use synthetic_prescriptions_all.json for all 35")
    parser.add_argument("--limit", type=int, help="only the first N cases (quick test)")
    parser.add_argument("--no-live", action="store_true", help="skip the per-section live audits; final audit only")
    parser.add_argument("--out", help="results folder (default: benchmark/results/<timestamp>)")
    parser.add_argument("--delete-after", action="store_true",
                        help="remove each model from the HF cache after its run, if this run downloaded it")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    cases_path = Path(args.cases).resolve()

    if args.worker:
        return run_worker(args.models[0], cases_path, Path(args.out), args.limit, not args.no_live)

    models = args.models or default_models()
    run_dir = Path(args.out) if args.out else BENCH / "results" / datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    if any(m != RULES_ONLY for m in models):
        import importlib.util
        if importlib.util.find_spec("bitsandbytes") is None:
            print("bitsandbytes is not installed, 4-bit loading will fail. Install it into this venv:\n"
                  f"  {sys.executable} -m pip install -U bitsandbytes", file=sys.stderr)
            return 2
    for number, model in enumerate(models, 1):
        print(f"\n=== [{number}/{len(models)}] {model}", flush=True)
        cached_before = model == RULES_ONLY or model_in_cache(model)
        command = [sys.executable, str(Path(__file__).resolve()), "--worker", "--models", model,
                   "--cases", str(cases_path), "--out", str(run_dir / f"{slug(model)}.json")]
        if args.limit:
            command += ["--limit", str(args.limit)]
        if args.no_live:
            command.append("--no-live")
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
        code = subprocess.call(command, cwd=str(ROOT), env=env)
        if code:
            print(f"  {model} exited with code {code} (finished cases, if any, are kept)")
        if args.delete_after and not cached_before:
            delete_from_cache(model)
        gc.collect()
    write_summary(run_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
