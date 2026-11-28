"""T01.3: do the current engine and vLLM give the same alerts?

The same section checks are sent to two running audit servers, one at a time, the way the app calls them
(POST /api/audit/section = layer 1, then POST /api/audit/review = layer 2). Cases: the 36 team-written test cases
of benchmark/live_review_probe.py (doctor 15 + suite 13 + clean 8), every written section: 99 section checks, each
checked by both layers. No patient data.

Only one model fits on the GPU at a time, so record one engine, restart the server on the other, record again:

    # 1. current engine (AUDIT_PROVIDER=local), started with .\\run_local.ps1
    python -m benchmark.vllm_equivalence record --out benchmark/results/equiv_local.json
    # 2. stop it, start vLLM in WSL2, set AUDIT_PROVIDER=vllm in .env, .\\run_local.ps1 again
    python -m benchmark.vllm_equivalence record --out benchmark/results/equiv_vllm.json ^
        --layer1-from benchmark/results/equiv_local.json
    # 3. compare
    python -m benchmark.vllm_equivalence compare benchmark/results/equiv_local.json benchmark/results/equiv_vllm.json

--layer1-from gives layer 2 the first engine's layer-1 texts ("already reported"), so a layer-1 difference does not
also show up as a layer-2 difference.

Agreement: a section check agrees when its alert list is the same: layer 1 = the set of (rule, entry) pairs,
layer 2 = the set of (type, entry) pairs shown. Wording of the "why" is compared too but not counted.
Target: at least 95% of section checks agree on each layer. The report lists every difference.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

_args, sys.argv[1:] = sys.argv[1:], []
from benchmark import doctor_probe, live_suite  # noqa: E402
from benchmark.clinical_reviewer_prototype import CLEAN  # noqa: E402

sys.argv[1:] = _args
TARGET = 0.95


def _post(base: str, path: str, payload: dict) -> tuple[dict, float]:
    req = urllib.request.Request(
        base.rstrip("/") + "/api/audit/" + path,
        json.dumps(payload).encode("utf-8"),
        {"Content-Type": "application/json"},
    )
    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=600) as resp:
        return json.loads(resp.read().decode("utf-8")), time.perf_counter() - started


def _cases() -> list[tuple[str, str, str, int, dict]]:
    return (
        [("doctor", t, s, a, e) for t, s, a, e in doctor_probe.CASES]
        + [("suite", t, s, a, e) for t, s, a, e, _x, _g in live_suite.CASES]
        + [("clean", t, s, a, e) for t, s, a, e in CLEAN]
    )


def record(base: str, out: Path, layer1_from: Path | None, limit: int | None) -> None:
    given = {}
    if layer1_from:
        for r in json.loads(layer1_from.read_text(encoding="utf-8"))["checks"]:
            given[r["key"]] = [w for _rule, _ev, w in r["layer1"]]
    health = json.loads(urllib.request.urlopen(base.rstrip("/") + "/health", timeout=60).read().decode("utf-8"))
    checks, cases = [], _cases()[:limit] if limit else _cases()
    for n, (group, title, sex, age, entries) in enumerate(cases, 1):
        patient = {
            "id": 996000 + n,
            "serial": f"EQV-{n:03d}",
            "name": f"Equivalence {n}",
            "gender": sex,
            "dob": date(date.today().year - age, 1, 1).isoformat(),
            "ageValue": age,
            "ageUnit": "years",
        }
        visit: dict = {"id": 1799400000000 + n}
        print(f"=== {n}. [{group}] {title}", flush=True)
        for section, key in live_suite.ORDER:
            if key not in entries:
                continue
            value = entries[key]
            visit[key] = value if key == "rxList" else [{"id": i + 1, "text": t} for i, t in enumerate(value)]
            payload = {"patient": patient, "visit": dict(visit), "section": section, "revision": 1}
            body, s1 = _post(base, "section", payload)
            layer1 = [a for a in (body.get("live_audit", {}).get("findings") or []) if isinstance(a, dict)]
            ckey = f"{n}:{section}"
            whys = given.get(ckey, [str(a.get("why", "")) for a in layer1])
            review, s2 = _post(base, "review", {**payload, "layer1": whys})
            rv = review.get("review", {})
            checks.append(
                {
                    "key": ckey,
                    "case": title,
                    "group": group,
                    "section": section,
                    "layer1": [[a.get("rule_id"), a.get("evidence", ""), a.get("why", "")] for a in layer1],
                    "review": [[p.get("type"), p.get("entry"), p.get("problem")] for p in rv.get("problems", [])],
                    "review_status": rv.get("status"),
                    "layer1_s": round(s1, 2),
                    "review_s": round(s2, 2),
                }
            )
            print(f"  {section}: L1 {len(layer1)} alerts {s1:.1f}s, L2 {len(rv.get('problems', []))} {s2:.1f}s")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"server": health, "checks": checks}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {out} ({len(checks)} section checks)")


def _alerts(check: dict, layer: str) -> set[tuple[str, str]]:
    if layer == "layer1":
        return {(str(r), str(ev).split("|", 1)[0].strip().casefold()) for r, ev, _w in check["layer1"]}
    return {(str(t), str(e).strip().casefold()) for t, e, _p in check["review"]}


def _median(xs: list[float]) -> float:
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else 0.0


def compare(a_path: Path, b_path: Path, report: Path) -> bool:
    a, b = (json.loads(p.read_text(encoding="utf-8")) for p in (a_path, b_path))
    a_by, b_by = {c["key"]: c for c in a["checks"]}, {c["key"]: c for c in b["checks"]}
    keys = [k for k in a_by if k in b_by]
    lines = [
        "# vLLM equivalence report (T01.3)",
        "",
        f"- Engine A: `{a_path.name}` — {a['server'].get('provider')} {a['server'].get('model')}",
        f"- Engine B: `{b_path.name}` — {b['server'].get('provider')} {b['server'].get('model')}",
        f"- Section checks compared: {len(keys)} (missing in one file: {len(set(a_by) ^ set(b_by))})",
        "",
        "| Layer | Same alert list | Share | Target 95% | Same wording too |",
        "|---|---|---|---|---|",
    ]
    ok, diffs = True, []
    for layer, name in (("layer1", "Layer 1 (fast check)"), ("review", "Layer 2 (clinical reviewer)")):
        same = [k for k in keys if _alerts(a_by[k], layer) == _alerts(b_by[k], layer)]
        wording = [k for k in same if a_by[k][layer] == b_by[k][layer]]
        share = len(same) / len(keys) if keys else 0.0
        ok &= share >= TARGET
        lines.append(
            f"| {name} | {len(same)} of {len(keys)} | {share:.1%} | {'pass' if share >= TARGET else 'FAIL'} "
            f"| {len(wording)} of {len(same)} |"
        )
        for k in keys:
            if k not in same:
                only_a = sorted(_alerts(a_by[k], layer) - _alerts(b_by[k], layer))
                only_b = sorted(_alerts(b_by[k], layer) - _alerts(a_by[k], layer))
                diffs.append(f"| {name.split(' (')[0]} | {k} {a_by[k]['case']} | {only_a or '-'} | {only_b or '-'} |")
    lines += [
        "",
        "| Speed (median s) | A | B |",
        "|---|---|---|",
        f"| Layer 1 | {_median([c['layer1_s'] for c in a['checks']]):.2f} | "
        f"{_median([c['layer1_s'] for c in b['checks']]):.2f} |",
        f"| Layer 2 | {_median([c['review_s'] for c in a['checks']]):.2f} | "
        f"{_median([c['review_s'] for c in b['checks']]):.2f} |",
        "",
        "## Every difference",
        "",
    ]
    lines += ["| Layer | Check | Only in A | Only in B |", "|---|---|---|---|", *diffs] if diffs else ["None."]
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    rec = sub.add_parser("record")
    rec.add_argument("--server", default="http://127.0.0.1:8765")
    rec.add_argument("--out", type=Path, required=True)
    rec.add_argument("--layer1-from", type=Path)
    rec.add_argument("--limit", type=int, help="first N cases only (smoke test)")
    cmp_ = sub.add_parser("compare")
    cmp_.add_argument("a", type=Path)
    cmp_.add_argument("b", type=Path)
    cmp_.add_argument("--report", type=Path, default=Path("docs/vllm_equivalence_report.md"))
    args = parser.parse_args()
    if args.cmd == "record":
        record(args.server, args.out, args.layer1_from, args.limit)
    else:
        sys.exit(0 if compare(args.a, args.b, args.report) else 1)


if __name__ == "__main__":
    main()
