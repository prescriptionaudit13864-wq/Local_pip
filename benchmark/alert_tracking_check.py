"""Check the doctor-wise records after a tracked pilot simulation (`pilot_simulation run --track`).

For every simulated doctor it reads backend/data/doctor_<id>_<Name>/patient_*/visit_*/alerts.json and checks:
- every doctor has a folder, with their patients under it and one visit per prescription;
- the alert outcomes the backend recorded (fixed / edited, still flagged / not acted on) against what the simulated
  doctors actually did (which alerted errors they fixed, and whether the re-check still flagged them).

    python -m benchmark.alert_tracking_check benchmark/results/pilot30/fixed_fp8_tracked.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

_args, sys.argv[1:] = sys.argv[1:], []
from benchmark.pilot_simulation import RIGHT_KIND, score  # noqa: E402

sys.argv[1:] = _args

DATA = Path("prescribex-app/backend/data")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("result", type=Path)
    a = ap.parse_args()
    res = json.loads(a.result.read_text(encoding="utf-8-sig"))
    s = score(res)
    rows, outcome = [], Counter()
    problems = []
    for name in res["doctors"]:
        did = name.lower().replace(" ", "_")
        folders = [p for p in DATA.glob(f"doctor_{did}_*") if p.is_dir()]
        if len(folders) != 1:
            problems.append(f"{name}: {len(folders)} doctor folders")
            continue
        patients = [p for p in folders[0].glob("patient_*") if p.is_dir()]
        visits = [v for p in patients for v in p.glob("visit_*") if v.is_dir()]
        alerts = [json.loads((v / "alerts.json").read_text(encoding="utf-8")) for v in visits if (v / "alerts.json").exists()]
        finals = sum((v / "99_finalPrescription.json").exists() for v in visits)
        tot = Counter()
        for al in alerts:
            for k, n in al["totals"].items():
                tot[k] += n
            for x in al["alerts"]:
                outcome[(x["source"], x["outcome"])] += 1
        evs = [e for e in res["events"] if e["doctor"] == name]
        fixes = sum(len(e.get("fixed", [])) for e in evs)
        rows.append({"doctor": name, "folder": folders[0].name, "patients": len(patients), "visits": len(visits),
                     "final_saved": finals, "alerts_shown": tot["shown"], "fixed": tot["fixed"],
                     "edited_still_flagged": tot["editedStillFlagged"], "not_acted_on": tot["notActedOn"],
                     "changed_not_rechecked": tot["changedNotRechecked"], "gone_line_not_edited": tot["goneLineNotEdited"],
                     "doctor_fixes_made": fixes})
        if len(patients) != len(res["assignment"][name]):
            problems.append(f"{name}: {len(patients)} patient folders for {len(res['assignment'][name])} prescriptions")
    print(json.dumps({"doctors_checked": len(rows), "problems": problems, "sim_fix_rounds": s["fixes"],
                      "outcomes_by_source": {f"{k[0]} / {k[1]}": v for k, v in sorted(outcome.items())},
                      "totals": {k: sum(r[k] for r in rows) for k in rows[0] if k not in ("doctor", "folder")} if rows else {},
                      "doctors": rows}, indent=1))


if __name__ == "__main__":
    main()
