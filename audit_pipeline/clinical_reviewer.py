"""Second audit layer: a clinical reviewer that reads the prescription written so far and judges one section.

Layer 1 (the fine-tuned model) only knows the rules it was trained on. The reviewer is the same loaded weights with the
fine-tune adapter switched off (the general Gemma 4 12B instruct model), so it keeps its general medical knowledge. It
gets the prescription as the app builds it, Drug_Facts from the drug database (RAG), the rule engine's findings as hints
it may reject, and what layer 1 already reported (so it does not repeat it). No hand-written rule decides anything:
the prompt describes what a careful physician looks for, and the model decides.

Prototype and tests: benchmark/clinical_reviewer_prototype.py (results in benchmark/results/).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

# Writing order of the app (frontend section key -> reviewer section name, prescription field, title).
SECTIONS: list[tuple[str, str, str, str]] = [
    ("chiefComplaints", "chief_complaint", "Chief_Complaint", "Chief complaints"),
    ("pastHistory", "past_history", "Past_History", "Past history"),
    ("investigations", "investigation", "Investigation", "Investigations"),
    ("diagnosis", "diagnosis", "Diagnosis", "Diagnosis"),
    ("rx", "medicine", "Medication", "Medicines"),
    ("advice", "advice", "Advice", "Advice"),
]
_CONTEXT_FIELDS = [
    ("Screening", "Health_Screening"),
    ("On examination", "On_Examination"),
    ("Systemic examination", "Systemic_Examination"),
    ("Investigation results", "Investigation_Results"),
    ("Antenatal notes", "ANC_Notes"),
]
_LIST_SPLIT = {"Medication", "Diagnosis", "Investigation", "Advice"}

SYSTEM = (
    "You are an experienced general physician in Bangladesh checking another doctor's prescription while it is "
    "being written. You report only real problems that a careful senior colleague would point out, you state "
    "medical facts only when you are sure of them, and you never invent problems."
)

TASK = """Check the {title} section ({names}). Use the rest of the prescription only as context.

This clinic's documentation standard (from its physicians):
- A complaint, finding, test or diagnosis about a body part that exists on both sides or more than once (ear, eye,
  limb, joint, breast, kidney, tooth...) must say which one (left, right, both, or which tooth); a missing side is
  reported as "documentation". Single organs (bladder, heart, stomach, uterus, liver...) have no side.
- Severity must be stated only for symptoms where it decides emergency care (for example chest pain, breathlessness,
  sudden severe headache, severe abdominal pain). Do not ask for the severity of ordinary complaints (itching,
  thirst, cough, fever, urinary symptoms, routine aches, an ordinary headache).
- An antibiotic course longer than 14 days without a stated reason is reported as "review".

Report a problem in this section when, for this patient:
- an entry is not possible or not plausible (age, sex, anatomy, or a duration that cannot be true)
- an entry cannot be acted on without information that changes management (for example which side of a paired organ, where a pain is, how severe a pain or symptom is when that decides urgency)
- an entry contradicts another section (a different side, organ or condition in complaint, test and diagnosis)
- a diagnosis leaves a complaint or an abnormal result unexplained, or does not fit the patient
- a test or medicine has no reason in this prescription, or is not suitable for this patient
- a medicine is given to the wrong patient: an adult dose to a child, or a medicine unsafe for this patient's sex, age or pregnancy
- a medicine has a dangerous interaction or contraindication (rely on the drug facts given)
- treatment, advice, follow-up or referral that this condition clearly needs is missing (only once that section is written)

Do NOT report:
- anything that belongs to a section not written yet ({unwritten}); the doctor has not reached it
- results of tests ordered today (results come later)
- spelling, abbreviations, dose notation such as 1+0+1, or how the entry is formatted
- extra detail that would not change what the doctor does for this patient
- how complete a diagnosis is: never call it premature, non-specific, unconfirmed, awaiting results, or missing other possible causes or sub-types
- any comment on a dose, frequency or duration, except an adult dose given to a child and the antibiotic rule above (the first check judges doses)
- anything already reported by the first check (listed below)
- a drug fact you are not sure of

Severity: "critical" (could harm the patient now), "review" (should be checked or corrected), "documentation" (information missing that is needed to interpret the record).
Type: the FIRST type in this list that fits (a test, diagnosis or medicine that does not fit the patient's sex or age
is "sex_age_anatomy", not "no_reason"):
  "sex_age_anatomy"   not possible for this patient's sex, age or anatomy
  "side_missing"      which side / which one is not stated for a body part that exists more than once
  "severity_missing"  severity not stated for a symptom where it decides emergency care
  "contradiction"     contradicts another section (side, organ, condition)
  "implausible"       a duration, timing or value that cannot be true
  "no_reason"         a test or medicine with no reason in this prescription
  "unexplained"       a diagnosis that leaves a complaint or abnormal result unexplained
  "unsafe_for_patient" an adult dose given to a child, or a medicine unsafe for this patient's sex, age or pregnancy
  "antibiotic_duration" an antibiotic course longer than 14 days without a stated reason
  "interaction"       a dangerous interaction between two medicines BOTH prescribed here, or a contraindication with this patient's condition
  "missing_care"      essential treatment, advice, follow-up or referral missing
  "dose"              any other dose, frequency or duration comment
  "other"             anything else
Go through the numbered entries of this section ONE BY ONE ({count} entries) and give a result for every one, even
when it has no problem. Answer with JSON only:
{{"entries": [{{"n": 1, "entry": "the exact entry", "problems": [{{"problem": "one short sentence", "severity": "critical|review|documentation", "type": "one type from the list"}}]}}, ... one item for each of the {count} entries]}}
An entry with no real problem has "problems": []."""

# Types shown to the doctor: the ones the local reviewer judged reliably in testing (benchmark/live_review_probe*.log,
# 2026-11-14). Its other opinions (dose comments, missing care, other) were often wrong on correct prescriptions; those
# are judged by the fine-tuned first check instead and are kept in the response as "hidden" for inspection.
# Second test (live_review_probe_v5.log): "interaction", "unexplained" and "no_reason" were mostly wrong on correct
# prescriptions and are judged by the first check (Rx.ddi/Rx.contra, Dx.cc.1, Inv.*), so they are hidden too.
SHOWN_TYPES = {
    "sex_age_anatomy",
    "side_missing",
    "severity_missing",
    "contradiction",
    "implausible",
    "unsafe_for_patient",
    "antibiotic_duration",
}
# Severity is asked of complaints, not of tests, diagnoses or advice.
_TYPE_SECTIONS = {"severity_missing": {"chiefComplaints"}}


def section_info(section_key: str) -> tuple[str, str, str, str]:
    for item in SECTIONS:
        if item[0] == section_key:
            return item
    raise ValueError(f"unknown section {section_key!r}")


def _entries(key: str, value: str) -> list[str]:
    if "\n" in value:
        parts = value.split("\n")
    elif key in _LIST_SPLIT:
        parts = value.split(", ")
    else:
        parts = [value]
    return [p.strip() for p in parts if p.strip()]


def prescription_text(row: Mapping[str, Any], upto: str | None = None) -> tuple[str, list[str]]:
    """The prescription in writing order, entries numbered; and the titles of sections not written yet."""
    lines = [f"Patient: age {row.get('Age') or 'not recorded'}, sex {row.get('Gender') or 'not recorded'}"]
    for title, key in _CONTEXT_FIELDS:
        value = str(row.get(key, "") or "").strip()
        if value:
            lines.append(f"{title}: {value}")
    unwritten: list[str] = []
    passed = False
    for skey, _name, field, title in SECTIONS:
        value = str(row.get(field, "") or "").strip()
        if passed and not value:
            unwritten.append(title.lower())
            continue
        if value:
            lines.append(f"{title}:")
            lines += [f"  {i}. {e}" for i, e in enumerate(_entries(field, value), 1)]
        elif not passed:
            lines.append(f"{title}: (none written)")
        if skey == upto:
            passed = True
    return "\n".join(lines), unwritten


def build_messages(
    row: Mapping[str, Any],
    section_key: str,
    *,
    drug_facts: str = "",
    hints: Iterable[str] = (),
    already: Iterable[str] = (),
) -> list[dict[str, str]]:
    _skey, name, _field, title = section_info(section_key)
    text, unwritten = prescription_text(row, upto=section_key)
    hints, already = list(hints), list(already)
    user = text
    if drug_facts:
        user += f"\n\nDrug facts (from the drug database):\n{drug_facts}"
    if hints:
        user += (
            "\n\nRule-checker hints (simple automatic rules, may be wrong; confirm only real problems):\n"
            + "\n".join(f"- {h}" for h in hints)
        )
    user += "\n\nAlready reported by the first check (do not repeat):\n" + (
        "\n".join(f"- {a}" for a in already) if already else "- nothing"
    )
    count = len(_entries(_field, str(row.get(_field, "") or ""))) or 1
    user += "\n\n" + TASK.format(title=title.lower(), names=name, unwritten=", ".join(unwritten) or "none", count=count)
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


VERIFY = """A colleague listed these possible problems in the {title} section of the prescription above:
{listing}

For each one, decide as a senior physician of this clinic: is it a real error that you would ask the prescribing doctor
to correct for this patient? In this clinic a missing side for a body part that exists on both sides or more than once,
and a missing severity for a symptom where severity decides emergency care (such as chest pain), are real
documentation errors; severity of ordinary complaints is not. A comment on how complete a diagnosis is (premature,
non-specific, other causes possible), or on a dose other than an adult dose for a child or an antibiotic longer than
14 days, is not a real error. Drop it if it is a usual practice (including a working diagnosis made before test results are back,
and a usual dose for the use), a matter of preference, a wrong medical claim, about a section not written yet, or a
detail that would not change care.
Answer with JSON only: {{"keep": [numbers of the real errors]}} (an empty list if none)."""


def build_verify_messages(
    row: Mapping[str, Any], section_key: str, problems: list[dict[str, str]], drug_facts: str = ""
) -> list[dict[str, str]]:
    """Second look by the same model: each listed problem is re-judged, and only those it keeps are shown. This
    replaces the alert-fatigue filters a hand-written rule would otherwise need."""
    _skey, _name, _field, title = section_info(section_key)
    text, _unwritten = prescription_text(row, upto=section_key)
    if drug_facts:
        text += f"\n\nDrug facts (from the drug database):\n{drug_facts}"
    listing = "\n".join(f"{i}. [{p['severity']}] {p['entry']}: {p['problem']}" for i, p in enumerate(problems, 1))
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": text + "\n\n" + VERIFY.format(title=title.lower(), listing=listing)},
    ]


def _first_json(text: str) -> Any:
    """The first complete JSON object in the answer; anything after it is ignored (the model sometimes closes with
    an extra bracket, which made the whole answer unreadable and its findings were lost)."""
    start = text.find("{")
    if start < 0:
        return None
    try:
        return json.JSONDecoder().raw_decode(text[start:])[0]
    except ValueError:
        return None


def parse_keep(text: str, count: int) -> set[int]:
    data = _first_json(text)
    keep = data.get("keep") if isinstance(data, Mapping) else None
    if not isinstance(keep, list):
        return set(range(1, count + 1))  # unreadable answer: keep everything rather than hide a real problem
    return {int(k) for k in keep if str(k).isdigit() and 1 <= int(k) <= count}


def in_section(row: Mapping[str, Any], section_key: str, problems: list[dict[str, str]]) -> list[dict[str, str]]:
    """Answer-format check, not a medical rule: keep problems whose entry is in the section being reviewed (the model
    sometimes repeats a problem of an earlier section). A problem with no entry (something missing) is kept."""
    _skey, _name, field, _title = section_info(section_key)
    text = str(row.get(field, "") or "").casefold()
    out = []
    for p in problems:
        entry = re.sub(r"\s+", " ", p.get("entry", "")).strip().casefold()
        if not entry or entry in ("n/a", "none", "not provided") or entry[:20] in text:
            out.append(p)
    return out


def parse_problems(text: str) -> list[dict[str, str]]:
    data = _first_json(text)
    if not isinstance(data, Mapping):
        return []
    # Entry-by-entry answer {"entries": [{"entry", "problems": [...]}]}: flatten, each problem keeps its entry.
    if isinstance(data.get("entries"), list):
        problems = [
            {**p, "entry": str(e.get("entry", ""))}
            for e in data["entries"]
            if isinstance(e, Mapping)
            for p in (e.get("problems") or [])
            if isinstance(p, Mapping)
        ]
    else:
        problems = data.get("problems", [])
    out = []
    for p in problems if isinstance(problems, list) else []:
        if not isinstance(p, Mapping) or not str(p.get("problem", "")).strip():
            continue
        severity = str(p.get("severity", "review")).strip().casefold()
        out.append(
            {
                "entry": str(p.get("entry", "")).strip(),
                "problem": str(p.get("problem", "")).strip(),
                "severity": severity if severity in {"critical", "review", "documentation"} else "review",
                "type": str(p.get("type", "other")).strip().casefold() or "other",
            }
        )
    return out


def split_shown(
    problems: list[dict[str, str]], section_key: str = ""
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """(shown, hidden) by the model's own type label: only the reliably judged types are shown."""

    def ok(p: dict[str, str]) -> bool:
        kind = p.get("type", "")
        return kind in SHOWN_TYPES and (kind not in _TYPE_SECTIONS or section_key in _TYPE_SECTIONS[kind])

    return [p for p in problems if ok(p)], [p for p in problems if not ok(p)]


def concrete_hints(candidates: Iterable[Mapping[str, Any]], section_code: str) -> list[str]:
    """Rule-engine candidates that state a finding. Most candidates are generic per-rule review prompts added to every
    prescription ("Review whether ...", "Rulebook check ..."); they state nothing, so they are not passed on."""
    out = []
    for c in candidates:
        if section_code and str(c.get("section", "")) not in ("", section_code):
            continue
        text = str(c.get("why_candidate") or c.get("description") or "").strip()
        if not text or re.match(r"(Review|Rulebook check|Determine|Check|Decide|Assess|Verify)\b", text):
            continue
        ev = str(c.get("evidence", "")).split(" | ")[0][:90]
        out.append(text + (f" ({ev})" if ev else ""))
    return list(dict.fromkeys(out))
