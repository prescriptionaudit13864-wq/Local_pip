"""How many doctors can use the audit server at once? (load test for AUDIT_PROVIDER=vllm)

Each simulated doctor writes the team-written test cases section by section, the way the app does: POST
/api/audit/section (layer 1, fast check), then POST /api/audit/review (layer 2, clinical reviewer). N doctors run at
the same time; each starts at a different case. No patient data.

    # app running with .\\run_local.ps1 (AUDIT_PROVIDER=vllm)
    python -m benchmark.vllm_load_test --doctors 1 4 8 16 --checks 24

For each number of doctors it prints and writes (docs/vllm_load_report.md) the median and 95th-percentile wait per
layer and the section checks finished per minute. Run it against AUDIT_PROVIDER=local too to see the difference.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import urllib.request
from datetime import date
from pathlib import Path

_args, sys.argv[1:] = sys.argv[1:], []
from benchmark import live_suite  # noqa: E402
from benchmark.vllm_equivalence import _cases, _post  # noqa: E402

sys.argv[1:] = _args


def _steps() -> list[tuple[str, dict, dict]]:
    """Every (section, patient, visit-so-far) of the test cases, in writing order."""
    steps = []
    for n, (_group, _title, sex, age, entries) in enumerate(_cases(), 1):
        patient = {
            "id": 997000 + n,
            "serial": f"LOAD-{n:03d}",
            "name": f"Load {n}",
            "gender": sex,
            "dob": date(date.today().year - age, 1, 1).isoformat(),
            "ageValue": age,
            "ageUnit": "years",
        }
        visit: dict = {"id": 1799500000000 + n}
        for section, key in live_suite.ORDER:
            if key in entries:
                value = entries[key]
                visit[key] = value if key == "rxList" else [{"id": i + 1, "text": t} for i, t in enumerate(value)]
                steps.append((section, patient, dict(visit)))
    return steps


def _pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))] if xs else 0.0


def run(base: str, doctors: int, checks: int, steps: list) -> dict:
    l1, l2, errors = [], [], []
    lock = threading.Lock()

    def doctor(d: int) -> None:
        for i in range(checks):
            section, patient, visit = steps[(d * 7 + i) % len(steps)]
            payload = {"patient": patient, "visit": visit, "section": section, "revision": 1}
            try:
                body, s1 = _post(base, "section", payload)
                whys = [str(a.get("why", "")) for a in body.get("live_audit", {}).get("findings") or []]
                _review, s2 = _post(base, "review", {**payload, "layer1": whys})
                with lock:
                    l1.append(s1)
                    l2.append(s2)
            except Exception as exc:  # noqa: BLE001 - count and report every failure
                with lock:
                    errors.append(f"{type(exc).__name__}: {exc}")

    started = time.perf_counter()
    threads = [threading.Thread(target=doctor, args=(d,)) for d in range(doctors)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.perf_counter() - started
    return {
        "doctors": doctors,
        "checks": len(l1),
        "errors": errors,
        "wall_s": round(wall, 1),
        "per_min": round(len(l1) / wall * 60, 1) if wall else 0.0,
        "l1_p50": round(_pct(l1, 0.5), 2),
        "l1_p95": round(_pct(l1, 0.95), 2),
        "l2_p50": round(_pct(l2, 0.5), 2),
        "l2_p95": round(_pct(l2, 0.95), 2),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--server", default="http://127.0.0.1:8765")
    ap.add_argument("--doctors", type=int, nargs="+", default=[1, 4, 8, 16])
    ap.add_argument("--checks", type=int, default=24, help="section checks per doctor")
    ap.add_argument("--report", type=Path, default=Path("docs/vllm_load_report.md"))
    a = ap.parse_args()
    health = json.loads(urllib.request.urlopen(a.server.rstrip("/") + "/health", timeout=60).read().decode())
    steps = _steps()
    rows = []
    for n in a.doctors:
        r = run(a.server, n, a.checks, steps)
        rows.append(r)
        print(
            f"{n:>3} doctors: {r['checks']} checks in {r['wall_s']}s = {r['per_min']}/min | layer 1 median "
            f"{r['l1_p50']}s p95 {r['l1_p95']}s | layer 2 median {r['l2_p50']}s p95 {r['l2_p95']}s | "
            f"errors {len(r['errors'])}",
            flush=True,
        )
    lines = [
        "# Load test: doctors at the same time",
        "",
        f"Server: {health.get('provider')} {health.get('model')}. Each doctor sends {a.checks} section checks "
        "(layer 1 then layer 2), team-written test cases, no patient data. Times are what one doctor waits.",
        "",
        "| Doctors at once | Section checks per minute | Layer 1 median | Layer 1 95th pct | Layer 2 median "
        "| Layer 2 95th pct | Errors |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['doctors']} | {r['per_min']} | {r['l1_p50']} s | {r['l1_p95']} s | {r['l2_p50']} s "
            f"| {r['l2_p95']} s | {len(r['errors'])} |"
        )
    errs = [e for r in rows for e in r["errors"]]
    if errs:
        lines += ["", "Errors (first 10):", ""] + [f"- {e}" for e in errs[:10]]
    a.report.parent.mkdir(parents=True, exist_ok=True)
    a.report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {a.report}")


if __name__ == "__main__":
    main()
