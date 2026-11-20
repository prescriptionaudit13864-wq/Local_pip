"""Does the writing order cause wrong alerts? Re-check one section of a live-suite case three ways:

  as live     only the sections written before it (what the app sends while the doctor is writing)
  complete    every section of the case filled in (what the training rows look like)
  repeated    'as live' sent a second time (is the answer stable?)

    python -m benchmark.order_probe 11 rx
    python -m benchmark.order_probe 13 rx

Prints each answer's alerts and the exact fields the model was given for that section.
"""

from __future__ import annotations

import json
import sys
from datetime import date

_args, sys.argv[1:] = sys.argv[1:], []
from benchmark.live_suite import CASES, ORDER, post  # noqa: E402


def visit_with(entries: dict, upto: str | None) -> dict:
    visit: dict = {"id": 1799000000000}
    for section, key in ORDER:
        if key in entries:
            value = entries[key]
            visit[key] = value if key == "rxList" else [{"id": i + 1, "text": t} for i, t in enumerate(value)]
        if upto and section == upto:
            break
    return visit


def main() -> None:
    n, target = int(_args[0]), _args[1]
    title, sex, age, entries, _exp, _gap = CASES[n - 1]
    patient = {
        "id": 970000 + n,
        "serial": f"ORDER-{n:03d}",
        "name": f"Order {n}",
        "gender": sex,
        "dob": date(date.today().year - age, 1, 1).isoformat(),
        "ageValue": age,
        "ageUnit": "years",
    }
    print(f"=== {n}. {title}: section {target}")
    for label, upto in (("as live", target), ("complete", None), ("repeated", target)):
        body, secs = post({"patient": patient, "visit": visit_with(entries, upto), "section": target, "revision": 1})
        live = body.get("live_audit", {})
        alerts = [(a.get("rule_id"), a.get("why")) for a in (live.get("findings") or []) if isinstance(a, dict)]
        print(f"\n-- {label} ({secs:.1f}s): {alerts or 'no alerts'}")
        if label != "repeated":
            fields = body.get("pipeline_input") or {}
            shown = (
                {k: v for k, v in fields.items() if isinstance(v, str) and v.strip()}
                if isinstance(fields, dict)
                else {}
            )
            print("   fields sent:", json.dumps(shown, ensure_ascii=False)[:700])


if __name__ == "__main__":
    main()
