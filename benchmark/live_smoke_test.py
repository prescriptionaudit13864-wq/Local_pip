"""Send test prescriptions to the running audit bridge exactly as the app does (POST /api/audit/section, one
section at a time, in writing order) and print each section's alerts and how long it took.

Start the app first (.\\run_local.ps1), then:
    python -m benchmark.live_smoke_test [http://127.0.0.1:8765]

Cases are the concerns the doctor raised (sex/age, severity, site, "recent", impossible timing, contradiction,
duplicate painkillers, an infant with weak advice) plus one clean prescription that should raise little or nothing.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from datetime import date

URL = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765").rstrip("/") + "/api/audit/section"
ORDER = ("chiefComplaints", "pastHistory", "investigations", "diagnosis", "rx", "advice")


def med(name, generic, strength, dosage, duration, instruction="", form="Tab."):
    return {"medicine": name, "generic": generic, "strength": strength, "dosage": dosage, "duration": duration,
            "instruction": instruction, "form": form}


CASES = [
    ("Female with a prostate complaint", "Female", 50, {"chiefComplaints": ["Difficulty passing urine due to prostate enlargement for 2 months"]}, "CC.demo"),
    ("5-year-old with pregnancy", "Female", 5, {"chiefComplaints": ["Pregnancy for 3 months"]}, "CC.demo"),
    ("Fever without severity", "Male", 30, {"chiefComplaints": ["Fever for 2 days"]}, "CC.2 (policy not yet set)"),
    ("Breast swelling without side", "Female", 35, {"chiefComplaints": ["Swelling of breast for 1 month"]}, "CC.4"),
    ("'Recent' as duration", "Male", 40, {"chiefComplaints": ["Recent cough"]}, "CC.3"),
    ("Impossible timing", "Male", 42, {"chiefComplaints": ["Weight loss for 1 day"]}, "CC.time"),
    ("Pregnant but amenorrhoea", "Female", 28, {"chiefComplaints": ["Amenorrhea for 2 months", "Pregnancy for 5 months"]}, "CC.context.conflict"),
    ("Napa + Rolac, no timing", "Male", 30, {
        "chiefComplaints": ["Headache for 2 days"], "diagnosis": ["Tension type headache"],
        "rxList": [med("Napa", "Paracetamol", "500 mg", "1+1+1", "3 days"), med("Rolac", "Ketorolac Tromethamine", "10 mg", "1+0+1", "3 days")],
        "adviceList": ["Take rest"]}, "Rx.dup, Rx.timing"),
    ("Infant cold, weak advice", "Male", 1, {
        "chiefComplaints": ["Runny nose for 2 days", "Cough for 2 days"], "diagnosis": ["Common cold"],
        "rxList": [med("Antista", "Chlorpheniramine Maleate", "2 mg/5 ml", "1/2 tsp twice", "5 days", "After meal", "Syrup")],
        "adviceList": ["Drink more water"]}, "Adv.cc (danger signs)"),
    ("Doctor's case: male with vaginal cramp", "Male", 30,
     {"chiefComplaints": ["Fever for 3 days", "body pain", "vaginal cramp"]},
     "CC.demo on 'vaginal cramp' only; CC.3 on 'body pain' and 'vaginal cramp'"),
    ("Doctor's case: female", "Female", 30,
     {"chiefComplaints": ["Fever for 3 days", "body pain", "vaginal cramp"]},
     "CC.3 on 'body pain' and 'vaginal cramp'; no CC.demo"),
    ("Clean adult UTI", "Male", 35, {
        "chiefComplaints": ["Burning micturition for 3 days"], "diagnosis": ["Acute cystitis"],
        "rxList": [med("Ciprocin", "Ciprofloxacin", "500 mg", "1+0+1", "5 days", "After meal")],
        "adviceList": ["Drink plenty of water", "Complete the full course of antibiotic"]}, "nothing or very little"),
]


def post(payload: dict) -> tuple[dict, float]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(URL, data=data, headers={"Content-Type": "application/json"})
    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=600) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body, time.perf_counter() - started


def alerts(live: dict) -> list[str]:
    out = []
    for key in ("errors", "findings", "possible_errors", "items"):
        for item in live.get(key) or []:
            if isinstance(item, dict):
                out.append(f"{key}: {item.get('rule_id', '?')} | {str(item.get('evidence', ''))[:70]} | {str(item.get('why') or item.get('message') or item.get('title') or '')[:90]}")
    return out


def main() -> None:
    timings = []
    for n, (title, gender, age, fields, expect) in enumerate(CASES, 1):
        dob = date(date.today().year - age, 1, 1).isoformat()
        patient = {"id": 900000 + n, "serial": f"LIVE-{n:03d}", "name": f"Live test {n}", "gender": gender,
                   "dob": dob, "ageValue": age, "ageUnit": "years"}
        visit = {"id": 1800000000000 + n}
        print(f"\n=== {n}. {title} ({gender}, {age} y) | expected: {expect}")
        for section in ORDER:
            key = {"chiefComplaints": "chiefComplaints", "diagnosis": "diagnosis", "rx": "rxList", "advice": "adviceList"}.get(section)
            if key is None or key not in fields:
                continue
            value = fields[key]
            visit[key] = value if key == "rxList" else [{"id": i + 1, "text": t} for i, t in enumerate(value)]
            try:
                body, secs = post({"patient": patient, "visit": dict(visit), "section": section, "revision": 1})
            except Exception as exc:  # noqa: BLE001
                print(f"  {section}: FAILED {exc}")
                continue
            live = body.get("live_audit", {})
            timings.append((section, secs))
            found = alerts(live)
            print(f"  {section}: {secs:.1f}s, status={live.get('status')}, {len(found)} alert(s)")
            for a in found:
                print("     ", a)
            if live.get("warnings"):
                print("      warnings:", str(live.get("warnings"))[:200])
    if timings:
        secs = sorted(t for _, t in timings)
        print(f"\nsection checks: {len(secs)} | median {secs[len(secs) // 2]:.1f}s | slowest {secs[-1]:.1f}s | "
              f"over 6s: {sum(t > 6 for t in secs)}")


if __name__ == "__main__":
    main()
