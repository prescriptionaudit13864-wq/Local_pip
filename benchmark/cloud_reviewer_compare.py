"""Compare a stronger reviewer model (cloud, through an OpenAI-compatible API) with the local one, on the same cases.

Same reviewer instructions (audit_pipeline/clinical_reviewer.py), same first pass + second look, same section-by-
section flow and the same layer-1 findings as the last local run (benchmark/results/live_review_probe.json), so the
only difference is the model. Cases are the synthetic test prescriptions (doctor's 15, live suite 13, correct 8):
no real patient data is sent.

    python -m benchmark.cloud_reviewer_compare                 (Groq, GROQ_MODEL from .env)
Writes benchmark/results/cloud_reviewer_compare.json.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

_args, sys.argv[1:] = sys.argv[1:], []
from benchmark import doctor_probe, live_suite  # noqa: E402
from benchmark.clinical_reviewer_prototype import CLEAN, build_row  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "benchmark" / "results" / "cloud_reviewer_compare.json"
LOCAL = ROOT / "benchmark" / "results" / "live_review_probe.json"


def client():
    from dotenv import load_dotenv
    from openai import OpenAI

    load_dotenv(ROOT / ".env", override=False)
    return OpenAI(
        api_key=os.environ["GROQ_API_KEY"], base_url="https://api.groq.com/openai/v1", max_retries=0, timeout=60
    ), os.environ.get("GROQ_MODEL", "qwen/qwen3.8-27b")


FAILED: list[str] = []


def ask(api, model: str, messages, prefix: str, max_tokens: int) -> tuple[str, float]:
    """One call, JSON answer. Paced for the free tier (8,000 tokens per minute) and retried until it answers, so a
    rate limit never turns into an empty "no problems" answer."""
    time.sleep(12)
    for attempt in range(12):
        try:
            started = time.perf_counter()
            resp = api.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
                extra_body={"reasoning_effort": "none"} if "qwen3" in model else {},
            )
            return resp.choices[0].message.content or "", time.perf_counter() - started
        except Exception as exc:  # noqa: BLE001
            text = str(exc)
            if "reasoning_effort" in text:
                model_kw = {}
                resp = api.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=0,
                    max_tokens=max_tokens,
                    response_format={"type": "json_object"},
                    **model_kw,
                )
                return resp.choices[0].message.content or "", 0.0
            wait = 20 + 10 * attempt
            time.sleep(wait)
    FAILED.append(str(messages[-1]["content"])[:60])
    print("      (NO ANSWER after retries)", flush=True)
    return "", 0.0


def main() -> None:
    from audit_pipeline.clinical_reviewer import (
        build_messages,
        build_verify_messages,
        in_section,
        parse_keep,
        parse_problems,
    )
    from audit_pipeline.finetune_prompt import _drug_facts

    api, model = client()
    local = {(r["n"], r["section"]): r for r in json.loads(LOCAL.read_text(encoding="utf-8"))} if LOCAL.exists() else {}
    cases = (
        [("doctor", t, s, a, e) for t, s, a, e in doctor_probe.CASES]
        + [("suite", t, s, a, e) for t, s, a, e, _x, _g in live_suite.CASES]
        + [("clean", t, s, a, e) for t, s, a, e in CLEAN]
    )
    print(f"model: {model}", flush=True)
    only = {int(a) for a in _args}  # case numbers to run (default: all)
    results, times = [], []
    for n, (group, title, sex, age, entries) in enumerate(cases, 1):
        if only and n not in only:
            continue
        print(f"\n=== {n}. [{group}] {title} ({sex}, {age} y)", flush=True)
        for section_key, list_key in live_suite.ORDER:
            if list_key not in entries:
                continue
            row = build_row(sex, age, entries, section_key)
            facts = _drug_facts(row) if section_key == "rx" else ""
            layer1 = [why for _rule, why in (local.get((n, section_key), {}).get("layer1") or [])]
            text, s1 = ask(api, model, build_messages(row, section_key, drug_facts=facts, already=layer1), "", 400)
            proposed = in_section(row, section_key, parse_problems(text))
            kept, s2 = proposed, 0.0
            if proposed:
                vtext, s2 = ask(api, model, build_verify_messages(row, section_key, proposed, facts), "", 80)
                keep = parse_keep(vtext, len(proposed))
                kept = [p for i, p in enumerate(proposed, 1) if i in keep]
            times.append(s1 + s2)
            local_review = local.get((n, section_key), {}).get("review", [])
            results.append(
                {
                    "n": n,
                    "group": group,
                    "case": title,
                    "section": section_key,
                    "secs": round(s1 + s2, 1),
                    "problems": kept,
                    "proposed": proposed,
                    "local_review": local_review,
                }
            )
            print(f"  {section_key} ({s1 + s2:.1f}s)", flush=True)
            for p in kept:
                print(f"      CLOUD {p['severity'][:4]:4} | {p['entry'][:35]} -> {p['problem'][:120]}", flush=True)
            for p in local_review:
                print(f"      local {p['severity'][:4]:4} | {p['entry'][:35]} -> {p['problem'][:120]}", flush=True)
        OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    times.sort()
    print(
        f"\nTIME per section review (cloud, model time only): median {times[len(times) // 2]:.1f}s, slowest {times[-1]:.1f}s"
    )
    print(f"calls with no answer: {len(FAILED)}")


if __name__ == "__main__":
    os.chdir(ROOT)
    main()
