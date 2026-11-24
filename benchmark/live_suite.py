"""Live test of every writing section, scored automatically. Run against the running app (.\\run_local.ps1):

    python -m benchmark.live_suite [http://127.0.0.1:8765]

Each case is sent the way the app sends it: POST /api/audit/section, one section at a time in writing order,
each section carrying everything written so far. Each expectation names a section, a rule family and (optionally)
the entered line the alert must point at. The score counts:
  caught      an expected alert that appeared on the right line
  missed      an expected alert that did not appear
  wrong       an alert that matches no expectation (a false alarm, a wrong line, or a problem from another
              section reported here)
Cases marked "known gap" are not counted in the score: they track problems whose fix is still pending.
Writes benchmark/results/live_suite.json.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

URL = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765").rstrip("/") + "/api/audit/section"
OUT = Path(__file__).resolve().parent / "results" / "live_suite.json"
ORDER = (
    ("chiefComplaints", "chiefComplaints"),
    ("pastHistory", "pastHistory"),
    ("investigations", "investigations"),
    ("diagnosis", "diagnosis"),
    ("rx", "rxList"),
    ("advice", "adviceList"),
)
# Rule families: the pipeline may report a check under an alias of the trained rule.
FAMILY = {
    "CC.demo": {"CC.demo", "CC.context.demo"},
    "CC.time": {"CC.time", "CC.time.plausibility"},
    "Hx.demo": {"Hx.context.demo"},
    "Dx.cc": {"Dx.cc.1", "Dx.cc.2"},
    "Rx.dose": {"Rx.demo", "Rx.dose.high", "Rx.dose.max", "Rx.dose.weight"},
    "Rx.dup": {"Rx.dup", "Rx.dup.class"},
    "Inv": {"Inv.dx", "Inv.cc", "Inv.hs", "Inv.relevance", "Inv.rag.missing"},
}


def med(name, generic, strength, dosage, duration, instruction="After meal", form="Tab."):
    return {
        "medicine": name,
        "generic": generic,
        "strength": strength,
        "dosage": dosage,
        "duration": duration,
        "instruction": instruction,
        "form": form,
    }


# (title, sex, age, entries by record key, expectations [(section, rule family, line text or None)], known_gap)
CASES = [
    (
        "Doctor's visit: male, wrong complaints, vague history",
        "Male",
        49,
        {
            "chiefComplaints": ["pregnancy for two days", "vaginal cramp", "fever"],
            "pastHistory": ["Allergic", "G/H: High Pressure", "Diabetic", "Surgical History"],
        },
        [
            ("chiefComplaints", "CC.demo", "pregnancy for two days"),
            ("chiefComplaints", "CC.demo", "vaginal cramp"),
            ("chiefComplaints", "CC.3", "vaginal cramp"),
            ("chiefComplaints", "CC.3", "fever"),
            ("pastHistory", "Hx.detail", "Allergic"),
            ("pastHistory", "Hx.detail", "Surgical History"),
        ],
        False,
    ),
    (
        # Reported from the vLLM test (Nov 2026): with three lines the section answer stopped at the pregnancy and
        # "Fever" lost its duration alert on both engines; the line's own answer flags it.
        "Male, pregnancy plus a complaint with no duration among others",
        "Male",
        49,
        {"chiefComplaints": ["Pregnancy for 3 days", "Fever", "knee joint pain for 3 weeks"]},
        [
            ("chiefComplaints", "CC.demo", "Pregnancy for 3 days"),
            ("chiefComplaints", "CC.3", "Fever"),
        ],
        False,
    ),
    (
        "Female-only history in a man",
        "Male",
        45,
        {"chiefComplaints": ["Cough for 5 days"], "pastHistory": ["Hysterectomy"]},
        [("pastHistory", "Hx.demo", "Hysterectomy")],
        False,
    ),
    (
        "Clean history",
        "Female",
        50,
        {
            "chiefComplaints": ["Headache for 3 days"],
            "pastHistory": ["Hypertension for 5 years", "Diabetes mellitus for 3 years"],
        },
        [],
        False,
    ),
    (
        "History longer than life",
        "Male",
        25,
        {"chiefComplaints": ["Thirst for 1 week"], "pastHistory": ["Diabetes for 30 years"]},
        [("pastHistory", "Hx.time.plausibility", "Diabetes for 30 years")],
        False,
    ),
    (
        "Diagnosis does not explain complaint",
        "Male",
        30,
        {"chiefComplaints": ["Itching all over body for 7 days"], "diagnosis": ["Acute bronchitis"]},
        [("diagnosis", "Dx.cc", None)],
        False,
    ),
    (
        "Clean diagnosis",
        "Female",
        28,
        {"chiefComplaints": ["Burning micturition for 3 days"], "diagnosis": ["Urinary tract infection"]},
        [],
        False,
    ),
    (
        "Suspected dengue without NS1",
        "Female",
        25,
        {
            "chiefComplaints": ["Fever for 3 days", "Headache for 3 days"],
            "investigations": ["Chest X-ray"],
            "diagnosis": ["Suspected dengue fever"],
        },
        [("investigations", "Inv", None)],
        False,
    ),
    (
        "Clean diabetes tests",
        "Male",
        55,
        {
            "chiefComplaints": ["Frequent urination for 2 months"],
            "investigations": ["HbA1c", "Fasting blood sugar"],
            "diagnosis": ["Type 2 diabetes mellitus"],
        },
        [],
        False,
    ),
    (
        "Adult paracetamol dose for a 4-year-old",
        "Male",
        4,
        {
            "chiefComplaints": ["Fever for 2 days"],
            "diagnosis": ["Fever"],
            "rxList": [med("Napa", "Paracetamol", "500 mg", "1+1+1", "3 days")],
            "adviceList": ["Sponge the body with water"],
        },
        [("rx", "Rx.dose", None)],
        False,
    ),
    (
        "Hyperlipidaemia without diet advice",
        "Female",
        52,
        {
            "chiefComplaints": ["Follow up"],
            "diagnosis": ["Hyperlipidaemia"],
            "rxList": [med("Atova", "Atorvastatin", "10 mg", "0+0+1", "1 month")],
            "adviceList": ["Come back with report"],
        },
        [("advice", "Adv.cc", None)],
        False,
    ),
    (
        "Clean adult UTI",
        "Male",
        35,
        {
            "chiefComplaints": ["Burning micturition for 3 days"],
            "investigations": ["Urine R/E"],
            "diagnosis": ["Acute cystitis"],
            "rxList": [med("Ciprocin", "Ciprofloxacin", "500 mg", "1+0+1", "5 days")],
            "adviceList": ["Drink plenty of water", "Complete the full course of antibiotic"],
        },
        [],
        False,
    ),
    (
        # Reported from the app (Nov 2026): a medicine with nothing after its strength was shown as clear. The
        # fine-tune has no rule for an oral medicine's missing schedule; the base model answers it per line.
        "Medicine with no dose, frequency or duration",
        "Female",
        30,
        {
            # the heartburn gives omeprazole its reason: without it the medicine-choice check rightly asks why
            "chiefComplaints": ["Fever for 3 days", "Heartburn for 1 week"],
            "diagnosis": ["Viral fever"],
            "rxList": [
                med("Ace", "Paracetamol", "500 mg", "", "", instruction=""),
                med("Seclo", "Omeprazole", "20 mg", "1+0+1", "14 days", instruction="Before meal", form="Cap."),
            ],
        },
        [("rx", "Rx.freq.missing", "Ace 500 mg"), ("rx", "Rx.duration.missing", "Ace 500 mg")],
        False,
    ),
    # Pilot rehearsal 2 (Nov 2026): what the fast check missed, now asked of the base model with the drug facts.
    (
        "Inhaler with no amount written",
        "Male",
        34,
        {
            "chiefComplaints": ["Shortness of breath for 3 days"],
            "pastHistory": ["Asthma for 6 years"],
            "diagnosis": ["Bronchial asthma"],
            "rxList": [med("Azmasol", "Salbutamol", "100 mcg", "", "30 days", instruction="As needed", form="Inhaler")],
        },
        [("rx", "Rx.freq.missing", "Azmasol")],
        False,
    ),
    (
        "Inhaler written in full (no meal timing needed)",
        "Female",
        29,
        {
            "chiefComplaints": ["Wheeze for 2 days"],
            "pastHistory": ["Asthma for 4 years"],
            "diagnosis": ["Bronchial asthma"],
            "rxList": [
                med("Azmasol", "Salbutamol", "100 mcg", "2 puffs when needed", "30 days", instruction="As needed",
                    form="Inhaler")
            ],
        },
        [],
        False,
    ),
    (
        "Two antihistamines together",
        "Female",
        27,
        {
            "chiefComplaints": ["Sneezing for 2 weeks"],
            "diagnosis": ["Allergic rhinitis"],
            "rxList": [
                med("Fexo", "Fexofenadine", "120 mg", "0+0+1", "14 days"),
                med("Alatrol", "Cetirizine", "10 mg", "0+0+1", "14 days"),
            ],
        },
        [("rx", "Rx.dup", "Alatrol")],
        False,
    ),
    (
        "Antibiotic with no infection",
        "Male",
        38,
        {
            "chiefComplaints": ["Headache for 5 days"],
            "diagnosis": ["Tension headache"],
            "rxList": [
                med("Napa", "Paracetamol", "500 mg", "1+0+1", "5 days"),
                med("Azithrocin", "Azithromycin", "500 mg", "1+0+0", "3 days"),
            ],
        },
        [("rx", "Rx.over", "Azithrocin")],
        False,
    ),
    (
        "NSAID with a peptic ulcer history",
        "Male",
        55,
        {
            "chiefComplaints": ["Knee pain for 3 weeks"],
            "pastHistory": ["Peptic ulcer for 3 years"],
            "diagnosis": ["Osteoarthritis of knee"],
            "rxList": [med("Naprosyn", "Naproxen", "500 mg", "1+0+1", "10 days")],
        },
        [("rx", "Rx.contra", "Naprosyn")],
        False,
    ),
    (
        "Warfarin with aspirin",
        "Male",
        62,
        {
            "chiefComplaints": ["Headache for 3 days"],
            "pastHistory": ["Atrial fibrillation for 2 years"],
            "diagnosis": ["Tension headache"],
            "rxList": [
                med("Warin", "Warfarin", "5 mg", "0+0+1", "30 days"),
                med("Ecosprin", "Aspirin", "300 mg", "1+0+1", "5 days"),
            ],
        },
        [("rx", "Rx.ddi", "Ecosprin")],
        False,
    ),
    (
        "Impossible duration",
        "Male",
        42,
        {"chiefComplaints": ["Weight loss for 1 day"]},
        [("chiefComplaints", "CC.time", "Weight loss for 1 day")],
        True,
    ),
    (
        "Infant cold, no danger-sign advice",
        "Male",
        1,
        {
            "chiefComplaints": ["Runny nose for 2 days"],
            "diagnosis": ["Common cold"],
            "rxList": [
                med("Antista", "Chlorpheniramine Maleate", "2 mg/5 ml", "1/2 tsp twice", "5 days", form="Syrup")
            ],
            "adviceList": ["Drink more water"],
        },
        [("advice", "Adv.cc", None)],
        True,
    ),
]


def post(payload: dict) -> tuple[dict, float]:
    req = urllib.request.Request(URL, json.dumps(payload).encode("utf-8"), {"Content-Type": "application/json"})
    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=600) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body, time.perf_counter() - started


def matches(alert: dict, section: str, family: str, line: str | None) -> bool:
    rules = FAMILY.get(family, {family})
    if alert["section"] != section or alert["rule_id"] not in rules:
        return False
    return line is None or line.casefold() in alert["evidence"].casefold()


def main() -> None:
    results, timings = [], []
    totals = {"caught": 0, "missed": 0, "wrong": 0}
    for n, (title, sex, age, entries, expected, gap) in enumerate(CASES, 1):
        patient = {
            "id": 950000 + n,
            "serial": f"SUITE-{n:03d}",
            "name": f"Suite {n}",
            "gender": sex,
            "dob": date(date.today().year - age, 1, 1).isoformat(),
            "ageValue": age,
            "ageUnit": "years",
        }
        visit: dict = {"id": 1797000000000 + n}
        alerts = []
        print(f"\n=== {n}. {title} ({sex}, {age} y){'  [known gap, not scored]' if gap else ''}")
        for section, key in ORDER:
            if key not in entries:
                continue
            value = entries[key]
            visit[key] = value if key == "rxList" else [{"id": i + 1, "text": t} for i, t in enumerate(value)]
            body, secs = post({"patient": patient, "visit": dict(visit), "section": section, "revision": 1})
            timings.append((section, secs))
            live = body.get("live_audit", {})
            found = [
                {
                    "section": section,
                    "rule_id": str(a.get("rule_id", "")),
                    "evidence": str(a.get("evidence", "")),
                    "why": str(a.get("why", "")),
                }
                if isinstance(a, dict)
                else {"section": section, "rule_id": "text", "evidence": str(a), "why": str(a)}
                for a in (live.get("findings") or []) + (live.get("possible_errors") or [])
            ]
            alerts += found
            print(f"  {section}: {secs:.1f}s, {len(found)} alert(s)")
            for a in found:
                print(f"      {a['rule_id']} | {a['evidence'][:80]}")
        caught = missed = 0
        for section, family, line in expected:
            # One alert may cover several expected lines ("no duration: vaginal cramp; fever").
            if any(matches(a, section, family, line) for a in alerts):
                caught += 1
            else:
                missed += 1
                print(f"  MISSED: {section} {family} {line or ''}")
        # An alert is wrong when it matches no expectation: a false alarm, a wrong line, or a leak.
        wrong = [a for a in alerts if not any(matches(a, s, f, w) for s, f, w in expected)]
        for a in wrong:
            print(f"  WRONG:  {a['section']} {a['rule_id']} | {a['evidence'][:80]}")
        if not gap:
            totals["caught"] += caught
            totals["missed"] += missed
            totals["wrong"] += len(wrong)
        results.append(
            {
                "case": title,
                "known_gap": gap,
                "caught": caught,
                "missed": missed,
                "wrong": [(a["section"], a["rule_id"], a["evidence"]) for a in wrong],
                "alerts": alerts,
            }
        )
    secs = sorted(t for _, t in timings)
    print(
        f"\nSCORE (excluding known gaps): caught {totals['caught']}, missed {totals['missed']}, wrong alerts {totals['wrong']}"
    )
    print(
        f"TIME: {len(secs)} section checks | median {secs[len(secs) // 2]:.1f}s | slowest {secs[-1]:.1f}s | "
        f"over 6s: {sum(t > 6 for t in secs)}"
    )
    by_section: dict[str, list[float]] = {}
    for section, t in timings:
        by_section.setdefault(section, []).append(t)
    print("  per section (median s):", {s: round(sorted(v)[len(v) // 2], 1) for s, v in by_section.items()})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps({"totals": totals, "results": results, "timings": timings}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
