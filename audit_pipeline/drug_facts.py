"""Drug facts for the model to judge medicines with: the reference lines a doctor would look up.

For each mapped medicine this writes, from the structured drug knowledge base (rag/drug_extracted_facts_merged.jsonl,
extracted from the medex.com.bd monographs) and generic.csv, short reference lines for the model's Drug_Facts input:

  - what it is and what was prescribed (generic, class, calculated daily dose, duration)
  - what it is used for, and the dose lines for the patient's age group (database age bands and routes)
  - for a woman of child-bearing age: the pregnancy and breastfeeding lines
  - its main contraindications and its major interactions
  - the physician's reviewed classification or note where she corrected the database

Nothing is chosen by matching words in the patient's record and nothing here decides whether something is wrong: the
model reads the patient's record and these lines, and judges (dose, pregnancy, interaction, contraindication,
medicine without indication).
"""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

from .medicine_facts import clean_monograph_text

DATA = Path(__file__).resolve().parent / "data"
FACTS_PATH = DATA / "rag" / "drug_extracted_facts_merged.jsonl"
GENERIC_PATH = DATA / "generic.csv"

MAX_LINE = 170  # characters per fact line
MAX_DOSE_LINES = 3  # dose lines per medicine
MAX_CHARS = 3600  # whole Drug_Facts text (every medicine keeps its safety lines; uses and doses share the rest)


def _short(text: Any, limit: int = MAX_LINE) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _years(value: Any) -> float | None:
    """'6 months' -> 0.5, '12 years' -> 12.0, None -> None."""
    match = re.match(r"\s*(\d+(?:\.\d+)?)\s*(year|month|week|day)", str(value or ""), re.IGNORECASE)
    if not match:
        return None
    n, unit = float(match.group(1)), match.group(2).casefold()
    return n / {"year": 1, "month": 12, "week": 52, "day": 365}[unit]


@lru_cache(maxsize=1)
def _index() -> tuple[dict[str, list[dict[str, Any]]], dict[str, str], dict[str, str]]:
    facts: dict[str, list[dict[str, Any]]] = {}
    if FACTS_PATH.exists():
        with open(FACTS_PATH, encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                    extra = json.loads(row.get("extra_json") or "{}")
                except ValueError:
                    continue
                gid = str(row.get("generic_id", "")).strip()
                if gid:
                    facts.setdefault(gid, []).append(
                        {"type": row.get("fact_type", ""), "text": row.get("text", ""), **extra}
                    )
    classes: dict[str, str] = {}
    uses: dict[str, str] = {}
    if GENERIC_PATH.exists():
        with open(GENERIC_PATH, encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                gid = str(row.get("generic id", "")).strip()
                classes[gid] = str(row.get("drug class", "") or "").strip()
                uses[gid] = clean_monograph_text(row.get("indication description", ""))
    return facts, classes, uses


OVERRIDES_PATH = DATA / "rag" / "physician_drug_overrides.json"
MAX_CONTRA = 4  # contraindication lines per medicine
MAX_INTERACTIONS = 4  # major-interaction lines per medicine
_PREGNANCY_WORDS = {
    "whole": "avoid throughout pregnancy",
    "first": "avoid in the 1st trimester (up to 13 weeks)",
    "from20": "avoid from 20 weeks of pregnancy",
    "third": "avoid in the 3rd trimester (from 28 weeks)",
    "none": "not a concern in pregnancy",
    "wrong": "limited evidence; use only when justified",
}


def _norm(name: Any) -> str:
    return re.sub(r"\s+", " ", str(name or "")).strip().casefold()


@lru_cache(maxsize=1)
def _overrides() -> dict[str, Any]:
    """The physician's review of the drug facts (LLM_Trainer/review_pages/physician_drugfacts_answers.json): her
    pregnancy classification per medicine, and her notes on contraindications and interactions she rejected."""
    empty: dict[str, Any] = {"pregnancy": {}, "contraindication_notes": {}, "interaction_not_major": {}}
    if not OVERRIDES_PATH.exists():
        return empty
    raw = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
    return {
        "pregnancy": {_norm(k): v for k, v in raw.get("pregnancy", {}).items()},
        "contraindication_notes": {_norm(k): v for k, v in raw.get("contraindication_notes", {}).items()},
        "interaction_not_major": {
            frozenset(map(_norm, p["drugs"])): p["note"] for p in raw.get("interaction_not_major", [])
        },
    }


def _age_group(age: float | None) -> set[str]:
    if age is None:
        return {"adult", "unspecified"}
    if age < 12:
        return {"child", "unspecified"}
    if age >= 65:
        return {"adult", "elderly", "geriatric", "unspecified"}
    return {"adult", "unspecified"}


_INJECTED_ROUTES = {
    "iv",
    "im",
    "intravenous",
    "intramuscular",
    "injection",
    "infusion",
    "parenteral",
    "subcutaneous",
    "sc",
}


def _dose_lines(rows: list[dict[str, Any]], age: float | None, form: str = "") -> list[str]:
    """Dose lines for the patient's age, using only the database's own fields (patient group, age band, route)."""
    groups = _age_group(age)
    injected_form = _norm(form).startswith(("injection", "infusion", "iv "))
    scored = []
    for r in rows:
        if r["type"] != "dose_rule" or str(r.get("patient_group") or "unspecified") not in groups:
            continue
        route = _norm(r.get("route"))
        if route and not injected_form and route in _INJECTED_ROUTES:
            continue  # an injection dose is not the reference for a tablet or syrup
        lo, hi = _years(r.get("age_min")), _years(r.get("age_max"))
        if age is not None and ((lo is not None and age < lo) or (hi is not None and age > hi)):
            continue
        in_band = age is not None and (lo is not None or hi is not None)
        scored.append((not in_band, not r.get("max_dose"), len(str(r["text"])), _short(r["text"])))
    seen, out = set(), []
    for *_, text in sorted(scored):
        if text not in seen:
            seen.add(text)
            out.append(text)
    return out[:MAX_DOSE_LINES]


def drug_facts_text(
    medicines: Sequence[Mapping[str, Any]],
    *,
    age: Any = None,
    gender: Any = "",
    record_text: str = "",  # kept for callers; facts are no longer chosen by words in the record
) -> str:
    """The reference lines for each prescribed medicine. Nothing is chosen by matching words in the record: every
    medicine shows its safety lines, and the model decides which apply to this patient."""
    facts, classes, uses = _index()
    over = _overrides()
    try:
        age_years = float(str(age).strip()) if str(age or "").strip() else None
    except ValueError:
        age_years = _years(age)
    reproductive = str(gender or "").strip()[:1].casefold() == "f" and (age_years is None or 12 <= age_years <= 50)
    mapped = [m for m in medicines if str(m.get("generic_id", "")).strip()]
    blocks = []
    for med in mapped:
        gid = str(med["generic_id"]).strip()
        rows = facts.get(gid, [])
        brand, generic = str(med.get("brand_name", "") or ""), str(med.get("generic_name", "") or "")
        cls = classes.get(gid, "")
        daily = med.get("calculated_daily_dose_mg")
        duration = re.search(
            r"-\s*\((\d+(?:\.\d+)?\s*(?:day|week|month|year)s?(?:\(s\))?)\)",
            str(med.get("raw_text", "")),
            re.IGNORECASE,
        )
        head = f"{brand} = {generic}" + (f" [{cls}]" if cls else "")
        if daily:
            head += f"; prescribed {float(daily):g} mg/day"
        if duration:
            head += f" for {duration.group(1)}"
        safety: list[str] = []
        if reproductive:
            reviewed = over["pregnancy"].get(_norm(generic))
            preg = next(
                (r for r in rows if r["type"] == "pregnancy_lactation" and r.get("population") == "pregnancy"), None
            )
            if reviewed:  # the physician's classification replaces the database line
                safety.append(
                    f"pregnancy (physician reviewed): {_PREGNANCY_WORDS.get(reviewed['decision'], reviewed['decision'])} - "
                    + _short(reviewed.get("note", ""), 150)
                )
            elif preg:
                safety.append(
                    f"pregnancy: {preg.get('recommendation', '')} - " + _short(preg.get("details") or preg["text"], 110)
                )
            lact = next(
                (r for r in rows if r["type"] == "pregnancy_lactation" and r.get("population") == "lactation"), None
            )
            if lact:
                safety.append(
                    f"breastfeeding: {lact.get('recommendation', '')} - "
                    + _short(lact.get("details") or lact["text"], 90)
                )
        groups = []
        for r in rows:
            group = (
                _short(r.get("contraindicated_condition_or_group") or "", 90) if r["type"] == "contraindication" else ""
            )
            if group and group not in groups:
                groups.append(group)
        safety += ["contraindicated: " + g for g in groups[:MAX_CONTRA]]
        safety += ["physician note: " + _short(n, 150) for n in over["contraindication_notes"].get(_norm(generic), [])]
        majors = []
        for r in rows:
            if r["type"] == "drug_interaction" and str(r.get("severity")) == "major":
                line = _short(r["text"], 130)
                if line not in majors:
                    majors.append(line)
        safety += ["major interaction: " + m for m in majors[:MAX_INTERACTIONS]]
        for other in mapped:
            note = over["interaction_not_major"].get(frozenset((_norm(generic), _norm(other.get("generic_name")))))
            if note and other is not med:
                safety.append(
                    f"physician note on {other.get('brand_name') or other.get('generic_name')}: " + _short(note, 140)
                )
        extra: list[str] = []  # lower priority: trimmed first when the prescription is long
        used = uses.get(gid) or next((r["text"] for r in rows if r["type"] == "indication"), "")
        if used:
            extra.append("used for: " + _short(used, 150))
        extra += ["dose: " + d for d in _dose_lines(rows, age_years, str(med.get("dosage_form", "")))]
        blocks.append((head, safety, extra))
    # Every medicine keeps its name and safety lines. A long prescription trims each medicine's "used for" and dose
    # lines to its fair share instead of cutting the text at a fixed length (that cut dropped the last medicine).
    share = MAX_CHARS // max(1, len(blocks))
    out = []
    for head, safety, extra in blocks:
        used_chars = len(head) + 1 + sum(len(x) + 1 for x in safety)
        kept = []
        for line in extra:
            room = share - used_chars - 1
            if room < 40:
                break
            line = line if len(line) <= room else _short(line, room)
            kept.append(line)
            used_chars += len(line) + 1
        out.append("\n".join([head, *kept, *safety]))
    return "\n".join(out)


__all__ = ["drug_facts_text"]
