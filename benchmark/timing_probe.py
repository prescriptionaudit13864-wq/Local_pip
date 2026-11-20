"""Replay chosen live-suite cases section by section and show where the time goes.

    python -m benchmark.timing_probe 1 11        (case numbers from live_suite.CASES)

For every section call it prints the wall time and the server's own diagnostics when the response carries them
(model output length, a preview of the raw answer, fast-path fallbacks), so a slow check can be traced to a long
answer, a fallback to normal decoding, or extra calls.
"""
from __future__ import annotations

import json
import sys
from datetime import date

_args, sys.argv[1:] = sys.argv[1:], []  # live_suite reads argv[1] as the server URL when imported
from benchmark.live_suite import CASES, ORDER, post  # noqa: E402

sys.argv[1:] = _args


def walk(node, path=""):
    """Yield (path, value) for small diagnostic-looking values anywhere in the response."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from walk(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        for i, v in enumerate(node[:6]):
            yield from walk(v, f"{path}[{i}]")
    else:
        key = path.rsplit(".", 1)[-1].lower()
        if any(w in key for w in ("time", "secs", "ms", "fallback", "count", "preview", "warning", "provider", "calls")):
            yield path, node


def main() -> None:
    wanted = {int(a) for a in sys.argv[1:]} or {1, 11}
    for n, (title, sex, age, entries, _expected, _gap) in enumerate(CASES, 1):
        if n not in wanted:
            continue
        patient = {"id": 960000 + n, "serial": f"PROBE-{n:03d}", "name": f"Probe {n}", "gender": sex,
                   "dob": date(date.today().year - age, 1, 1).isoformat(), "ageValue": age, "ageUnit": "years"}
        visit: dict = {"id": 1798000000000 + n}
        print(f"\n=== {n}. {title}")
        for section, key in ORDER:
            if key not in entries:
                continue
            value = entries[key]
            visit[key] = value if key == "rxList" else [{"id": i + 1, "text": t} for i, t in enumerate(value)]
            body, secs = post({"patient": patient, "visit": dict(visit), "section": section, "revision": 1})
            print(f"  {section}: {secs:.1f}s   response keys: {sorted(body)[:12]}")
            for path, val in walk(body):
                text = json.dumps(val, ensure_ascii=False)
                print(f"      {path} = {text[:300]}")


if __name__ == "__main__":
    main()
