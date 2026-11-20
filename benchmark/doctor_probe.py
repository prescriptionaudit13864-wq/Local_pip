"""The project doctor's usability cases (2026-11-14), sent to the running app section by section.

    python -m benchmark.doctor_probe

Prints every alert per section, so each reported problem can be traced: not flagged, flagged once instead of per
entry, flagged twice under two titles, or order-dependent.
"""
from __future__ import annotations

import sys
from datetime import date

_args, sys.argv[1:] = sys.argv[1:], []
from benchmark.live_suite import ORDER, med, post  # noqa: E402

CASES = [
    ("CC: loss of weight for 1 day", "Male", 40, {"chiefComplaints": ["Loss of weight for 1 day"]}),
    ("CC: increased weight for 1 day", "Female", 30, {"chiefComplaints": ["Increased weight for 1 day"]}),
    ("CC: weight gain 6 kg in 1 day", "Female", 30, {"chiefComplaints": ["Weight gain 6 kg in 1 day"]}),
    ("CC: elbow pain (no side, no duration)", "Male", 30, {"chiefComplaints": ["Elbow pain"]}),
    ("CC: ear pain (no side)", "Male", 30, {"chiefComplaints": ["Ear pain for 3 days"]}),
    ("CC/Inv/Dx: elbow, sides disagree", "Male", 35, {
        "chiefComplaints": ["Elbow pain for 3 days"], "investigations": ["X-ray of right elbow"],
        "diagnosis": ["Left elbow joint pain"]}),
    ("CC: pregnancy in a man + headache with no duration", "Male", 40,
     {"chiefComplaints": ["Pregnancy for 2 months", "Headache"]}),
    ("CC: severe chest pain without severity details", "Male", 55, {"chiefComplaints": ["Chest pain for 2 hours"]}),
    ("Inv: PSA for a woman", "Female", 45, {"chiefComplaints": ["Lower abdominal pain for 1 week"],
                                            "investigations": ["PSA (Prostate Specific Antigen)"]}),
    ("Inv: Pap smear for a man", "Male", 45, {"chiefComplaints": ["Burning micturition for 3 days"],
                                              "investigations": ["Pap smear"]}),
    ("Dx: male, GDM first", "Male", 30, {"chiefComplaints": ["Increased thirst for 1 month"],
                                         "diagnosis": ["Gestational diabetes mellitus", "Pregnancy in 3rd trimester"]}),
    ("Dx: male, pregnancy first", "Male", 30, {"chiefComplaints": ["Increased thirst for 1 month"],
                                               "diagnosis": ["Pregnancy in 3rd trimester", "Gestational diabetes mellitus"]}),
    ("Rx: antibiotic with no infection", "Male", 35, {
        "chiefComplaints": ["Headache for 2 days"], "diagnosis": ["Tension headache"],
        "rxList": [med("Napa", "Paracetamol", "500 mg", "1+0+1", "3 days"),
                   med("Azithrocin", "Azithromycin", "500 mg", "1+0+0", "3 days")]}),
    ("Rx: antibiotic for 30 days", "Female", 30, {
        "chiefComplaints": ["Burning micturition for 3 days"], "diagnosis": ["Urinary tract infection"],
        "rxList": [med("Ciprocin", "Ciprofloxacin", "500 mg", "1+0+1", "30 days")]}),
    ("Rx: antibiotic twice daily, gastric medicine once", "Male", 40, {
        "chiefComplaints": ["Burning micturition for 3 days"], "diagnosis": ["Urinary tract infection"],
        "rxList": [med("Ciprocin", "Ciprofloxacin", "500 mg", "1+0+1", "7 days"),
                   med("Seclo", "Omeprazole", "20 mg", "0+0+1", "7 days", instruction="Before meal")]}),
]


def main() -> None:
    for n, (title, sex, age, entries) in enumerate(CASES, 1):
        patient = {"id": 980000 + n, "serial": f"DOC-{n:03d}", "name": f"Doctor case {n}", "gender": sex,
                   "dob": date(date.today().year - age, 1, 1).isoformat(), "ageValue": age, "ageUnit": "years"}
        visit: dict = {"id": 1799100000000 + n}
        print(f"\n=== {n}. {title} ({sex}, {age} y)")
        for section, key in ORDER:
            if key not in entries:
                continue
            value = entries[key]
            visit[key] = value if key == "rxList" else [{"id": i + 1, "text": t} for i, t in enumerate(value)]
            body, secs = post({"patient": patient, "visit": dict(visit), "section": section, "revision": 1})
            live = body.get("live_audit", {})
            found = [a for a in (live.get("findings") or []) if isinstance(a, dict)]
            print(f"  {section} ({secs:.1f}s): {'no alert' if not found else ''}")
            for a in found:
                print(f"      [{a.get('rule_id')}] {str(a.get('why', ''))[:150]}")


if __name__ == "__main__":
    main()
