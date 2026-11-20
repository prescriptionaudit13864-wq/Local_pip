"""End-to-end test of both audit layers through the running app, the way the frontend calls them.

For every case and every written section: POST /api/audit/section (layer 1, the fine-tuned model), then
POST /api/audit/review with layer 1's findings (layer 2, the clinical reviewer). Cases: the doctor's 15, the 13
live-suite cases and the 8 correct prescriptions of benchmark/clinical_reviewer_prototype.py.

    python -m benchmark.live_review_probe          (the app must be running: .\\run_local.ps1)
Writes benchmark/results/live_review_probe.json.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

_args, sys.argv[1:] = sys.argv[1:], []
from benchmark import doctor_probe, live_suite  # noqa: E402
from benchmark.clinical_reviewer_prototype import CLEAN  # noqa: E402

BASE = "http://127.0.0.1:8765/api/audit/"
OUT = Path(__file__).resolve().parent / "results" / "live_review_probe.json"


def post(path: str, payload: dict) -> tuple[dict, float]:
    req = urllib.request.Request(BASE + path, json.dumps(payload).encode("utf-8"), {"Content-Type": "application/json"})
    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=600) as resp:
        return json.loads(resp.read().decode("utf-8")), time.perf_counter() - started


def main() -> None:
    cases = (
        [("doctor", t, s, a, e) for t, s, a, e in doctor_probe.CASES]
        + [("suite", t, s, a, e) for t, s, a, e, _x, _g in live_suite.CASES]
        + [("clean", t, s, a, e) for t, s, a, e in CLEAN]
    )
    results, t1, t2 = [], [], []
    for n, (group, title, sex, age, entries) in enumerate(cases, 1):
        patient = {
            "id": 995000 + n,
            "serial": f"REV-{n:03d}",
            "name": f"Review {n}",
            "gender": sex,
            "dob": date(date.today().year - age, 1, 1).isoformat(),
            "ageValue": age,
            "ageUnit": "years",
        }
        visit: dict = {"id": 1799300000000 + n}
        print(f"\n=== {n}. [{group}] {title} ({sex}, {age} y)", flush=True)
        for section, key in live_suite.ORDER:
            if key not in entries:
                continue
            value = entries[key]
            visit[key] = value if key == "rxList" else [{"id": i + 1, "text": t} for i, t in enumerate(value)]
            payload = {"patient": patient, "visit": dict(visit), "section": section, "revision": 1}
            body, s1 = post("section", payload)
            layer1 = [a for a in (body.get("live_audit", {}).get("findings") or []) if isinstance(a, dict)]
            review, s2 = post("review", {**payload, "layer1": [a.get("why", "") for a in layer1]})
            problems = review.get("review", {}).get("problems", [])
            t1.append(s1)
            t2.append(s2)
            results.append(
                {
                    "n": n,
                    "group": group,
                    "case": title,
                    "section": section,
                    "layer1_s": round(s1, 1),
                    "review_s": round(s2, 1),
                    "layer1": [(a.get("rule_id"), a.get("why")) for a in layer1],
                    "review": problems,
                    "review_status": review.get("review", {}).get("status"),
                }
            )
            print(f"  {section}: fast {s1:.1f}s, review {s2:.1f}s", flush=True)
            for a in layer1:
                print(f"      L1  [{a.get('rule_id')}] {str(a.get('why', ''))[:130]}", flush=True)
            for p in problems:
                print(
                    f"      REV {p['severity'][:4]:4} {p.get('type', '')[:14]:14} | {p['entry'][:32]} -> {p['problem'][:110]}",
                    flush=True,
                )
            for p in review.get("review", {}).get("hidden", []) or []:
                print(
                    f"      hid {p['severity'][:4]:4} {p.get('type', '')[:14]:14} | {p['entry'][:32]} -> {p['problem'][:90]}",
                    flush=True,
                )
        OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    for name, t in (("fast check", t1), ("clinical review", t2)):
        t.sort()
        print(
            f"{name}: median {t[len(t) // 2]:.1f}s, slowest {t[-1]:.1f}s, over 6s: {sum(x > 6 for x in t)} of {len(t)}"
        )


if __name__ == "__main__":
    main()
