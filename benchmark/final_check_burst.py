"""Worst case for the final check: every doctor presses "AI Audit" (POST /audit-api/audit) at the same instant.

Each simulated doctor signs in through the pilot link and sends one different, completed prescription from the
rehearsal set (`pilot_simulation.make_critical`). All requests are released together by a barrier. Prints when each
doctor got the result, the wait (median / 95% / slowest), errors caught by the final check and the GPU load.

    python -m benchmark.final_check_burst --link https://<words>.trycloudflare.com --codes codes.json --out result.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import time
from datetime import date, datetime
from pathlib import Path

_args, sys.argv[1:] = sys.argv[1:], []
from benchmark.engine_audit import GpuSampler  # noqa: E402
from benchmark.live_suite import ORDER  # noqa: E402
from benchmark.pilot_simulation import RIGHT_KIND, Doctor, make_critical  # noqa: E402

sys.argv[1:] = _args


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--link", required=True)
    ap.add_argument("--codes", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=11)
    a = ap.parse_args()
    codes = json.loads(a.codes.read_text(encoding="utf-8"))
    names = list(codes)
    rxs = make_critical(n=5 * len(names), seed=a.seed)[: len(names)]   # one different prescription per doctor
    doctors = [Doctor(a.link, codes[n], n) for n in names]
    for d in doctors:
        d.sign_in({"name": d.name, "degrees": "MBBS", "speciality": "Medicine"})
    barrier = threading.Barrier(len(doctors))
    results = [None] * len(doctors)
    gpu = GpuSampler()

    def press(i: int) -> None:
        p, d = rxs[i], doctors[i]
        visit: dict = {"id": 1799990000000 + i}
        for _section, key in ORDER:
            if p.get(key):
                visit[key] = p[key] if key == "rxList" else [{"id": j + 1, "text": t} for j, t in enumerate(p[key])]
        payload = {"patient": {"id": 994000 + i, "serial": p["id"], "name": p["name"], "gender": p["sex"],
                               "dob": date(date.today().year - p["age"], 1, 1).isoformat(), "ageValue": p["age"],
                               "ageUnit": "years"},
                   "visit": visit, "history": [], "live_audits": {}}
        barrier.wait()
        t0 = time.time()
        try:
            raw, secs = d._req("/audit-api/audit", payload, timeout=900)
            report = json.loads(raw).get("report", {})
            found = [f for items in (report.get("errors_by_section") or {}).values() for f in items or []]
            results[i] = {"doctor": d.name, "rx": p["id"], "sent": t0, "secs": secs, "findings": len(found),
                          "found": [[str(f.get("rule_id", "")), str(f.get("evidence", "")) + " " + str(f.get("why", ""))]
                                    for f in found], "expected": p["expected"]}
        except Exception as exc:  # noqa: BLE001
            results[i] = {"doctor": d.name, "rx": p["id"], "sent": t0, "error": f"{type(exc).__name__}: {exc}"}

    gpu.start()
    threads = [threading.Thread(target=press, args=(i,)) for i in range(len(doctors))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    gpu.stop.set()
    gpu.join()
    ok = [r for r in results if r and "error" not in r]
    secs = sorted(r["secs"] for r in ok)
    caught = total = 0
    for r in ok:
        for _s, line, ty in r["expected"]:
            total += 1
            kinds = RIGHT_KIND.get(ty)
            caught += any(line.casefold() in text.casefold() and (kinds is None or rule in kinds) for rule, text in r["found"])
    out = {"link": a.link, "doctors": len(doctors), "results": results, "gpu": gpu.summary(),
           "sent_at": datetime.fromtimestamp(min(r["sent"] for r in results)).strftime("%H:%M:%S"),
           "first_s": round(secs[0], 1) if secs else None, "median_s": round(statistics.median(secs), 1) if secs else None,
           "p95_s": round(secs[min(len(secs) - 1, int(0.95 * (len(secs) - 1)))], 1) if secs else None,
           "last_s": round(secs[-1], 1) if secs else None, "failed": len(results) - len(ok),
           "caught": caught, "errors": total}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "results"}, indent=1))
    for r in sorted(ok, key=lambda r: r["secs"]):
        print(f"  {r['doctor']}: {r['rx']} result after {r['secs']:.1f}s ({r['findings']} findings)")


if __name__ == "__main__":
    main()
