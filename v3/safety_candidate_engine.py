"""Generate provisional medicine-safety candidates from structured evidence.

Candidates always require later confirmation. This module does not produce
final audit findings and performs no fuzzy medicine or condition matching.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Mapping, Sequence

from .frequency_parser import normalize_digits
from .text_translation import translated_text


_WARNING_TERMS = re.compile(
    r"\b(caution|contraindicat\w*|risk|avoid|not recommended|"
    r"consult (?:a )?(?:doctor|physician)|f[oe]tal harm|unsafe)\b",
    re.IGNORECASE,
)
_PEDIATRIC_TERMS = re.compile(
    r"\b(caution|contraindicat\w*|not recommended|avoid|"
    r"not established|under \d+|below (?:the )?age|younger than|age restriction)\b",
    re.IGNORECASE,
)
_PREGNANCY_TERMS = re.compile(
    r"\b(pregnan(?:t|cy)|gestation|f[oe]tal|trimester)\b", re.IGNORECASE
)
_LACTATION_TERMS = re.compile(
    r"\b(lactat(?:ing|ion)|breast[ -]?feed(?:ing)?|nursing mother)\b",
    re.IGNORECASE,
)
_RENAL_TERMS = re.compile(
    r"\b(renal|kidney|eGFR|GFR|CrCl|creatinine clearance)\b",
    re.IGNORECASE,
)
_HEPATIC_TERMS = re.compile(
    r"\b(hepatic|liver|cirrhosis|jaundice|SGPT|ALT|AST|bilirubin)\b",
    re.IGNORECASE,
)
_PEDIATRIC_CONTEXT_TERMS = re.compile(
    r"\b(child(?:ren)?|pediatric|paediatric|infant|neonat|adolescent|under \d+)\b",
    re.IGNORECASE,
)
_ELDERLY_TERMS = re.compile(
    r"\b(elderly|geriatric|older (?:adult|patient)s?|advanced age|aged \d+)\b",
    re.IGNORECASE,
)
_SAFETY_ACTION_TERMS = re.compile(
    r"\b(caution|contraindicat\w*|avoid|not recommended|dose adjust\w*|"
    r"adjust(?:ment)? of (?:the )?dose|reduce(?:d)? dose|dosage reduction|"
    r"dose (?:should|must) be reduced|monitor(?:ing)?|use only if|"
    r"should not be used|risk|unsafe|f[oe]tal harm|consult (?:a )?physician)\b",
    re.IGNORECASE,
)
_NEGATED_RISK_TERMS = re.compile(
    r"\b(no dose adjustment (?:is )?required|does not require dose adjustment|"
    r"without dose adjustment|safe (?:for|in)|no known risk)\b",
    re.IGNORECASE,
)
_ALLERGY_FACT_TERMS = re.compile(
    r"\b(allerg(?:y|ic)|hypersensitiv\w*|contraindicat\w*)\b",
    re.IGNORECASE,
)
_DAILY_MG_DOSE = re.compile(
    r"(?P<context>.{0,80}?)(?P<value>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>mg|g|gm|gram|grams)\b(?P<tail>[^,.;\n]{0,40})",
    re.IGNORECASE,
)
_MAX_DOSE_CONTEXT = re.compile(
    r"\b(max(?:imum)?|not exceed|do not exceed|up to|should not exceed)\b",
    re.IGNORECASE,
)
_USUAL_DOSE_CONTEXT = re.compile(
    r"\b(usual|recommended|standard|adult|typical|normally)\b",
    re.IGNORECASE,
)
_DAILY_DOSE_CONTEXT = re.compile(
    r"\b(?:daily|total daily|maximum daily|usual daily|recommended daily)\s+dose\b|"
    r"\bdose\s+(?:per|/)\s+day\b",
    re.IGNORECASE,
)
_DAILY_MG_RANGE = re.compile(
    r"(?P<context>.{0,100}?)(?P<low>\d+(?:\.\d+)?)\s*(?:-|to)\s*"
    r"(?P<high>\d+(?:\.\d+)?)\s*(?P<unit>mg|g|gm|gram|grams)\b"
    r"(?P<tail>[^.;\n]{0,100}?\b(?:daily|per\s+day|/day)\b)",
    re.IGNORECASE,
)
_PER_DOSE_FREQUENCY_TAIL = re.compile(
    r"^\s*(?:"
    r"(?:once|twice|three|four|five|six|\d+(?:\.\d+)?)\s+(?:times?\s+)?daily|"
    r"(?:times?\s+)?daily|"
    r"(?:every|q)\s*\d+\s*(?:hours?|hrs?|h)\b|"
    r"\d+\s*hourly\b|"
    r"t\.?i\.?d\.?|b\.?i\.?d\.?|q\.?i\.?d\.?"
    r")",
    re.IGNORECASE,
)
_COUNTED_FREQUENCY_TAIL = re.compile(
    r"^\s*(?:"
    r"(?:once|twice|three|four|five|six|\d+(?:\.\d+)?)\s+(?:times?\s+)?daily|"
    r"(?:every|q)\s*\d+\s*(?:hours?|hrs?|h)\b|"
    r"\d+\s*hourly\b|"
    r"t\.?i\.?d\.?|b\.?i\.?d\.?|q\.?i\.?d\.?"
    r")",
    re.IGNORECASE,
)
_STRONG_CLASS_KEYWORDS = {
    "antacid",
    "antiplatelet",
    "anti-platelet",
    "anticoagulant",
    "anti-coagulant",
    "benzodiazepine",
    "fluoroquinolone",
    "macrolide",
    "maoi",
    "nsaid",
    "opioid",
    "ssri",
    "tetracycline",
}
_CLASS_DUPLICATION_TERMS = {
    "nsaid",
    "non-steroidal anti-inflammatory",
    "non steroidal anti inflammatory",
    "benzodiazepine",
    "opioid",
    "ssri",
    "anticoagulant",
    "antiplatelet",
}
_FOOD_CONFLICT_TERMS = re.compile(
    r"\b(milk|dairy|calcium|antacid|food)\b|দুধ", re.IGNORECASE
)
_BACTERIAL_OR_ANTIBIOTIC_FACT = re.compile(
    r"\b(antibiotic|antibacterial|susceptible organisms?|bacterial|macrolide|"
    r"cephalosporin|penicillin|tetracycline|quinolone)\b",
    re.IGNORECASE,
)
_FREQUENCY_FACT_PATTERNS: tuple[tuple[re.Pattern[str], float], ...] = (
    (re.compile(r"\b(?:three|3)\s+times\s+daily\b|\bt\.?i\.?d\.?\b", re.IGNORECASE), 3),
    (re.compile(r"\b(?:twice|2)\s+daily\b|\bb\.?i\.?d\.?\b", re.IGNORECASE), 2),
    (re.compile(r"\b(?:once|1)\s+daily\b|\bo\.?d\.?\b", re.IGNORECASE), 1),
    (re.compile(r"\bevery\s+8\s+hours?\b|\b8\s*hourly\b", re.IGNORECASE), 3),
    (re.compile(r"\bevery\s+6\s+hours?\b|\b6\s*hourly\b", re.IGNORECASE), 4),
    (re.compile(r"\bevery\s+12\s+hours?\b|\b12\s*hourly\b", re.IGNORECASE), 2),
)
_FREQUENCY_INTERVALS: tuple[tuple[re.Pattern[str], str, float], ...] = (
    (re.compile(r"\bevery\s+6\s+hours?\b|\bq\s*6\s*h\b|\b6\s*hourly\b|\bfour\s+times\s+daily\b|\b4\s+times\s+daily\b|\bq\.?i\.?d\.?\b", re.IGNORECASE), "q6h_like", 4),
    (re.compile(r"\bevery\s+8\s+hours?\b|\bq\s*8\s*h\b|\b8\s*hourly\b|\bthree\s+times\s+daily\b|\b3\s+times\s+daily\b|\bt\.?i\.?d\.?\b", re.IGNORECASE), "q8h_like", 3),
    (re.compile(r"\bevery\s+12\s+hours?\b|\bq\s*12\s*h\b|\b12\s*hourly\b|\btwice\s+daily\b|\b2\s+times\s+daily\b|\bb\.?i\.?d\.?\b", re.IGNORECASE), "q12h_like", 2),
    (re.compile(r"\bonce\s+daily\b|\b1\s+time\s+daily\b|\bo\.?d\.?\b", re.IGNORECASE), "daily_like", 1),
)
_PER_DOSE_MG = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>mg|g|gm|gram|grams)\b"
    r"(?P<tail>[^,.;\n]{0,80})",
    re.IGNORECASE,
)
_BASE_TOKEN_IGNORE = {
    "acid",
    "acetate",
    "calcium",
    "hydrochloride",
    "monohydrate",
    "potassium",
    "sodium",
    "sulfate",
}


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())



def _escaped_compact(item: str) -> str:
    """Regex-escaped ``item`` after the original (unchanged) ``\\s+`` substitution."""
    return re.escape(re.sub(r'\\s+', '', item))


def _contains_phrase(text: str, phrase: str) -> bool:
    phrase = _text(phrase).casefold()
    if not phrase:
        return False
    normalized_text = re.sub(r"[-\s]+", " ", text.casefold())
    normalized_phrase = re.sub(r"[-\s]+", " ", phrase.casefold())
    variants = {normalized_phrase}
    if normalized_phrase.endswith("s"):
        variants.add(normalized_phrase[:-1])
    elif normalized_phrase:
        variants.add(normalized_phrase + "s")
    return any(
        re.search(rf"(?<!\w){re.escape(item)}(?!\w)", normalized_text) is not None
        for item in variants
    ) or any(
        # (kept outside the f-string: backslashes inside f-string expressions need Python 3.12+)
        re.search(rf"(?<!\w){_escaped_compact(item)}s?(?!\w)", re.sub(r"\s+", "", normalized_text)) is not None
        for item in variants
        if len(re.sub(r"\s+", "", item)) >= 6
    )


def _generic_match_terms(generic_name: str) -> set[str]:
    name = _text(generic_name).casefold()
    if not name:
        return set()
    terms = {name}
    for component in re.split(r"\s*\+\s*|\s*/\s*", name):
        component = component.strip()
        if component:
            terms.add(component)
        for token in re.findall(r"[a-z][a-z0-9-]+", component):
            if len(token) >= 4 and token not in _BASE_TOKEN_IGNORE:
                terms.add(token)
    return terms


def _fact_index(
    fact_results: Sequence[Mapping[str, Any]],
) -> dict[str, list[dict[str, str]]]:
    indexed: dict[str, list[dict[str, str]]] = defaultdict(list)
    for result in fact_results:
        generic_id = _text(result.get("generic_id", ""))
        if not generic_id:
            continue
        for fact in result.get("facts", []):
            if not isinstance(fact, Mapping):
                continue
            text = _text(fact.get("text", ""))
            fact_type = _text(fact.get("fact_type", ""))
            if text and fact_type:
                indexed[generic_id].append(
                    {
                        "fact_type": fact_type,
                        "text": text,
                        "source_field": _text(fact.get("source_field", "")),
                        "source_type": _text(fact.get("source_type", "")),
                        "condition": _text(fact.get("condition", "")),
                        "condition_aliases": fact.get("condition_aliases", []),
                    }
                )
    return indexed


def _candidate(
    medicine: Mapping[str, Any],
    fact: Mapping[str, str],
    rule_name: str,
    why: str,
    *,
    section: str,
) -> dict[str, Any]:
    return {
        "section": section,
        "rule_name": rule_name,
        "generic_id": _text(medicine.get("generic_id", "")),
        "generic_name": _text(medicine.get("generic_name", "")),
        "severity": "needs_review",
        "trigger_source": "medicine_fact",
        "why_candidate": why,
        "evidence": fact["text"],
        "source_field": fact["source_field"],
        "needs_llm_confirmation": True,
        "rag_supported": True,
        "evidence_strength": _evidence_strength_for_rule(rule_name),
        "must_review_if_rejected": _evidence_strength_for_rule(rule_name) == "high",
    }


def _evidence_strength_for_rule(rule_name: str) -> str:
    name = _text(rule_name).casefold()
    if any(
        marker in name
        for marker in (
            "dose_exceeds_max",
            "dose_exceeds_usual",
            "pregnancy_safety",
            "lactation_safety",
            "allergy_contraindication",
            "renal_risk",
            "hepatic_risk",
            "hyperkalemia",
            "ddi",
            "contraindication",
        )
    ):
        return "high"
    if any(marker in name for marker in ("frequency", "duration", "food", "pediatric", "strength")):
        return "medium"
    return "low"


def _apply_candidate_strength(candidate: dict[str, Any], rule_name: str | None = None) -> dict[str, Any]:
    name = rule_name or _text(candidate.get("rule_name", ""))
    strength = _evidence_strength_for_rule(name)
    candidate.setdefault("rag_supported", bool(candidate.get("medicine_fact_evidence") or candidate.get("source_field")))
    candidate.setdefault("evidence_strength", strength)
    candidate.setdefault("must_review_if_rejected", strength == "high")
    return candidate


def _dose_candidate(
    medicine: Mapping[str, Any],
    fact: Mapping[str, str],
    rule_name: str,
    why: str,
) -> dict[str, Any]:
    result = _candidate(medicine, fact, rule_name, why, section="Rx")
    result.update(
        {
            "calculated_daily_dose_mg": medicine.get("calculated_daily_dose_mg"),
            "calculated_primary_daily_dose_mg": medicine.get("calculated_primary_daily_dose_mg"),
            "primary_component_name": medicine.get("primary_component_name"),
            "strength_mg": medicine.get("strength_mg"),
            "primary_strength_mg": medicine.get("primary_strength_mg"),
            "units_per_day": medicine.get("units_per_day"),
            "administrations_per_day": medicine.get("administrations_per_day"),
            "max_units_per_administration": medicine.get("max_units_per_administration"),
            "interval_pattern": medicine.get("interval_pattern"),
            "medicine_fact_evidence": fact["text"],
            "evidence": _text(medicine.get("raw_text", "")),
        }
    )
    return result



def _is_liquid_strength_text(value: Any) -> bool:
    return bool(re.search(r"/\s*\d*(?:\.\d+)?\s*ml\b", _text(value), re.IGNORECASE))

def _strength_mismatch_candidate(medicine: Mapping[str, Any]) -> dict[str, Any] | None:
    if not _text(medicine.get("generic_id", "")):
        return None
    written = medicine.get("written_strength_mg")
    reference = medicine.get("reference_strength_mg")
    try:
        written_mg = float(written)
        reference_mg = float(reference)
    except (TypeError, ValueError):
        return None
    if written_mg <= 0 or reference_mg <= 0 or abs(written_mg - reference_mg) < 0.001:
        return None
    if _is_liquid_strength_text(medicine.get("reference_strength", medicine.get("strength", ""))) and not _is_liquid_strength_text(medicine.get("written_strength", "")):
        return None
    if written_mg <= reference_mg * 2:
        return None
    raw = _text(medicine.get("raw_text", ""))
    generic = _text(medicine.get("generic_name", "medicine"))
    result = {
        "section": "Rx",
        "rule_name": "medicine_strength_unusual_or_mismatch_candidate",
        "generic_id": _text(medicine.get("generic_id", "")),
        "generic_name": generic,
        "severity": "needs_review",
        "trigger_source": "medicine_mapper",
        "why_candidate": (
            f"Written strength {written_mg:g} mg for {generic} does not match "
            f"the mapped/reference product strength {reference_mg:g} mg; this may be a documentation typo or unusual strength."
        ),
        "evidence": raw,
        "source_field": "Medication",
        "needs_llm_confirmation": True,
        "written_strength_mg": written_mg,
        "reference_strength_mg": reference_mg,
        "strength_mg": medicine.get("strength_mg"),
        "units_per_day": medicine.get("units_per_day"),
        "calculated_daily_dose_mg": medicine.get("calculated_daily_dose_mg"),
        "medicine_fact_evidence": "",
    }
    return _apply_candidate_strength(result, "medicine_strength_unusual_or_mismatch_candidate")


def _risk_candidate(
    medicine: Mapping[str, Any],
    fact: Mapping[str, str],
    risk: Mapping[str, Any],
    rule_name: str,
) -> dict[str, Any]:
    patient_evidence = _text(risk.get("evidence", ""))
    medicine_evidence = fact["text"]
    risk_type = _text(risk.get("risk_type", ""))
    result = _candidate(
        medicine,
        fact,
        rule_name,
        (
            f"Patient {risk_type} evidence and generic-specific medicine "
            "safety evidence require review."
        ),
        section="Rx",
    )
    result.update(
        {
            "patient_risk_type": risk_type,
            "patient_evidence": patient_evidence,
            "medicine_fact_evidence": medicine_evidence,
            "evidence": (
                f"Patient evidence: {patient_evidence} | "
                f"Medicine fact: {medicine_evidence}"
            ),
        }
    )
    return result


_SEVERITY_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3, "needs_review": 4}


def _join_unique(values: list[str], separator: str = " | ") -> str:
    return separator.join(dict.fromkeys(value for value in values if _text(value)))


def _merge_patient_risk_candidate(
    existing: dict[str, Any],
    incoming: Mapping[str, Any],
) -> None:
    existing_severity = _text(existing.get("severity", ""))
    incoming_severity = _text(incoming.get("severity", ""))
    if _SEVERITY_RANK.get(incoming_severity, 0) > _SEVERITY_RANK.get(existing_severity, 0):
        existing["severity"] = incoming_severity

    patient_evidence = _join_unique(
        [_text(existing.get("patient_evidence", "")), _text(incoming.get("patient_evidence", ""))]
    )
    fact_evidences = list(existing.get("medicine_fact_evidences", []) or [])
    fact_evidences.append(_text(incoming.get("medicine_fact_evidence", "")))
    fact_evidences = list(dict.fromkeys(value for value in fact_evidences if value))
    source_fields = list(existing.get("source_fields", []) or [])
    source_fields.append(_text(incoming.get("source_field", "")))
    source_fields = list(dict.fromkeys(value for value in source_fields if value))

    existing["patient_evidence"] = patient_evidence
    existing["medicine_fact_evidences"] = fact_evidences
    existing["medicine_fact_evidence"] = _join_unique(fact_evidences)
    existing["source_fields"] = source_fields
    existing["source_field"] = _join_unique(source_fields, separator="; ")
    existing["evidence"] = (
        f"Patient evidence: {patient_evidence} | "
        f"Medicine facts: {_join_unique(fact_evidences)}"
    )
    existing["why_candidate"] = (
        "Patient risk evidence and multiple generic-specific medicine safety "
        "facts require consolidated review."
    )


def _consolidate_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    consolidated: list[dict[str, Any]] = []
    patient_risk_index: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    seen_other: set[tuple[str, str, str, str]] = set()
    for item in candidates:
        patient_risk_type = _text(item.get("patient_risk_type", ""))
        if patient_risk_type:
            key = (
                _text(item.get("section", "")),
                _text(item.get("rule_id", "")) or _text(item.get("rule_name", "")),
                _text(item.get("rule_name", "")),
                _text(item.get("generic_id", "")),
                patient_risk_type,
            )
            existing = patient_risk_index.get(key)
            if existing is None:
                copy = dict(item)
                copy["medicine_fact_evidences"] = [
                    _text(item.get("medicine_fact_evidence", ""))
                ]
                copy["source_fields"] = [_text(item.get("source_field", ""))]
                patient_risk_index[key] = copy
                consolidated.append(copy)
            else:
                _merge_patient_risk_candidate(existing, item)
            continue

        key = (
            _text(item.get("rule_name", "")),
            _text(item.get("generic_id", "")),
            _text(item.get("evidence", "")),
            _text(item.get("source_field", "")),
        )
        if key not in seen_other:
            seen_other.add(key)
            consolidated.append(item)
    return consolidated


def _daily_dose_limits(text: str) -> tuple[list[float], list[float]]:
    max_values: list[float] = []
    usual_values: list[float] = []
    for match in _DAILY_MG_RANGE.finditer(text):
        context = _text(match.group("context"))
        tail = _text(match.group("tail"))
        if _COUNTED_FREQUENCY_TAIL.search(tail):
            continue
        value = float(match.group("high"))
        if match.group("unit").casefold() in {"g", "gm", "gram", "grams"}:
            value *= 1000
        if _MAX_DOSE_CONTEXT.search(context):
            max_values.append(value)
        else:
            usual_values.append(value)
    for match in _DAILY_MG_DOSE.finditer(text):
        context = _text(match.group("context"))
        tail = _text(match.group("tail"))
        if re.search(r"(?:/|\bper\s+)kg\b", tail, re.IGNORECASE):
            continue
        explicit_daily_after_value = bool(
            re.search(r"^\s*(?:/|per\s+)?(?:day|daily)\b", tail, re.IGNORECASE)
        )
        explicit_daily_context = bool(_DAILY_DOSE_CONTEXT.search(context))
        if not explicit_daily_after_value and not explicit_daily_context:
            continue
        if _COUNTED_FREQUENCY_TAIL.search(tail):
            continue
        value = float(match.group("value"))
        if match.group("unit").casefold() in {"g", "gm", "gram", "grams"}:
            value *= 1000
        if _MAX_DOSE_CONTEXT.search(context):
            max_values.append(value)
        elif _USUAL_DOSE_CONTEXT.search(context):
            usual_values.append(value)
    return max_values, usual_values


_NON_SPECIFIC_FACT_CONDITIONS = {
    "",
    "all",
    "all patients",
    "medicine",
    "dosing guidance",
    "reserve antibiotic",
    "adult",
    "adults",
    "children",
    "child",
}
_DOSE_CONDITION_PREFIX = re.compile(
    r"^\s*(?P<condition>[A-Z][A-Za-z][A-Za-z\s/,&()\\-]{2,70}?)\s*:\s*"
)


def _normalized_context_for_condition(value: Any) -> str:
    text = translated_text(normalize_digits(value))
    text = re.sub(r"[^a-z0-9\u0980-\u09ff\s]+", " ", text.casefold())
    return re.sub(r"\s+", " ", text).strip()


def _fact_condition_terms(fact: Mapping[str, Any]) -> list[str]:
    terms: list[str] = []
    structured = _text(fact.get("condition", ""))
    if structured:
        terms.append(structured)
    aliases = fact.get("condition_aliases", [])
    if isinstance(aliases, Sequence) and not isinstance(aliases, (str, bytes)):
        terms.extend(_text(alias) for alias in aliases if _text(alias))
    elif _text(aliases):
        terms.append(_text(aliases))
    text = _text(fact.get("text", ""))
    match = _DOSE_CONDITION_PREFIX.search(text)
    if match:
        prefix = _text(match.group("condition"))
        if not re.search(r"\b(?:adult|child(?:ren)?|pediatric|paediatric|dose|recommended|max(?:imum)?)\b", prefix, re.IGNORECASE):
            terms.append(prefix)
    normalized: list[str] = []
    for term in terms:
        norm = _normalized_context_for_condition(term)
        if norm and norm not in _NON_SPECIFIC_FACT_CONDITIONS and norm not in normalized:
            normalized.append(norm)
    return normalized


def _dose_fact_matches_prescription_context(
    fact: Mapping[str, Any],
    prescription_context: Mapping[str, Any] | None,
) -> bool:
    terms = _fact_condition_terms(fact)
    if not terms:
        return True
    if not prescription_context:
        return False
    context = _normalized_context_for_condition(
        " ".join(
            _text(prescription_context.get(key, ""))
            for key in (
                "Diagnosis",
                "Diagnosis ",
                "Chief_Complaint",
                "Chief Complaint",
                "Past_History",
                "Past History",
                "Health_Screening",
                "Health Screening",
                "On_Examination",
                "On Examination",
                "Systemic_Examination",
                "Systemic Examination",
                "Investigation_Results",
                "Investigation Results",
            )
        )
    )
    return any(_contains_phrase(context, term) for term in terms)


def _fact_matches_patient_group(
    fact: Mapping[str, str],
    patient_context: Mapping[str, Any],
) -> bool:
    text = _text(fact.get("text", ""))
    is_child = bool(patient_context.get("is_child"))
    child_specific = bool(
        re.search(r"\b(children?|pediatric|paediatric|infant|months?|years?)\b", text, re.IGNORECASE)
        and not re.search(r"\badults?\b", text, re.IGNORECASE)
    )
    adult_specific = bool(
        re.search(r"\badults?\b", text, re.IGNORECASE)
        and not re.search(r"\b(children?|pediatric|paediatric|infant)\b", text, re.IGNORECASE)
    )
    if is_child and adult_specific:
        return False
    if not is_child and child_specific:
        return False
    return True


def _has_weight_context(patient_context: Mapping[str, Any]) -> bool:
    text = _text(patient_context)
    return bool(re.search(r"\b(weight|wt)\b.{0,20}\d+(?:\.\d+)?\s*kg\b", text, re.IGNORECASE))


def _dose_review_candidate(
    medicine: Mapping[str, Any],
    facts: Sequence[Mapping[str, str]],
    patient_context: Mapping[str, Any],
    prescription_context: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    components = medicine.get("component_strengths")
    has_multiple_components = isinstance(components, list) and len(components) > 1
    daily_value = medicine.get("calculated_primary_daily_dose_mg") if has_multiple_components else None
    if daily_value is None:
        daily_value = medicine.get("calculated_daily_dose_mg")
    try:
        daily_mg = float(daily_value)
    except (TypeError, ValueError):
        return None
    dose_facts = [
        fact
        for fact in facts
        if fact.get("fact_type") == "dosage"
        and _fact_matches_patient_group(fact, patient_context)
        and _dose_fact_matches_prescription_context(fact, prescription_context)
    ]
    if not dose_facts:
        return None
    if patient_context.get("is_child") and not _has_weight_context(patient_context):
        return _dose_candidate(
            medicine,
            dose_facts[0],
            "needs_weight_for_dose_review_candidate",
            "Pediatric dose calculation needs weight; dose fact exists but weight is missing.",
        )
    for fact in dose_facts:
        max_values, usual_values = _daily_dose_limits(fact["text"])
        if max_values and daily_mg > min(max_values):
            candidate = _dose_candidate(
                medicine,
                fact,
                "dose_exceeds_max_candidate",
                (
                    f"Calculated daily dose {daily_mg:g} mg/day exceeds "
                    f"RAG-supported maximum daily dose {min(max_values):g} mg/day."
                ),
            )
            candidate["rag_max_daily_dose_mg"] = min(max_values)
            return candidate
        if usual_values and not max_values and daily_mg > min(usual_values):
            candidate = _dose_candidate(
                medicine,
                fact,
                "dose_exceeds_usual_or_max_candidate",
                (
                    f"Calculated daily dose {daily_mg:g} mg/day exceeds "
                    f"RAG-supported usual adult daily dose {min(usual_values):g} mg/day."
                ),
            )
            candidate["rag_usual_daily_dose_mg"] = min(usual_values)
            return candidate
        if usual_values and max_values and min(usual_values) < daily_mg <= min(max_values):
            candidate = _dose_candidate(
                medicine,
                fact,
                "high_daily_dose_review_candidate",
                (
                    f"Calculated daily dose {daily_mg:g} mg/day exceeds "
                    f"RAG-supported usual adult daily dose {min(usual_values):g} mg/day "
                    "but does not exceed the stated maximum."
                ),
            )
            candidate["rag_usual_daily_dose_mg"] = min(usual_values)
            candidate["rag_max_daily_dose_mg"] = min(max_values)
            return candidate
    return None


def _expected_frequency_detail(text: str) -> tuple[float | None, str]:
    for pattern, interval, times in _FREQUENCY_INTERVALS:
        if pattern.search(text):
            return times, interval
    return None, ""


def _has_strict_frequency_requirement(text: str) -> bool:
    """Return True only when a fact presents frequency as a regimen requirement.

    Plain dose-option text such as "0.5-1 gm 4 times daily" is useful for
    max/usual dose review, but it should not force a pattern warning by itself.
    """
    return bool(
        re.search(
            r"\b(regimen|schedule|protocol|required|must|strictly|fixed\s+course)\b|"
            r"\b(?:should|is\s+to)\s+be\s+(?:given|administered|taken)\s+"
            r"(?:every|q\s*)\s*\d+\s*(?:hours?|hrs?|h)\b|"
            r"\bevery\s+\d+\s*(?:hours?|hrs?|h)\s+for\s+",
            text,
            re.IGNORECASE,
        )
    )


def _per_dose_amounts(text: str) -> list[float]:
    values: list[float] = []
    for match in _PER_DOSE_MG.finditer(text):
        tail = _text(match.group("tail"))
        if not _PER_DOSE_FREQUENCY_TAIL.search(tail):
            continue
        value = float(match.group("value"))
        if match.group("unit").casefold() in {"g", "gm", "gram", "grams"}:
            value *= 1000
        values.append(value)
    return values


_COMPLEX_REGIMEN_MARKERS = re.compile(
    r"\b(?:or|alternatively)\b|"
    r"\b(?:then|followed\s+by|thereafter|subsequently)\b|"
    r"\bday\s*1\b|\bdays?\s*\d+\s*(?:-|to)\s*\d+\b|"
    r"\bloading\s+dose\b|\bmaintenance\s+dose\b|"
    r"\bsingle\s+dose\b",
    re.IGNORECASE,
)


def _is_complex_regimen_fact(text: str) -> bool:
    """Identify evidence that cannot safely be reduced to one daily amount.

    Alternative, loading/maintenance, and day-dependent schedules describe a
    complete course rather than a universal per-day ceiling.  The scalar dose
    checker still enforces an explicitly stated maximum; this guard prevents a
    maintenance phase from being misreported as that maximum.
    """
    return bool(_COMPLEX_REGIMEN_MARKERS.search(text))


def _rag_regimen_candidate(
    medicine: Mapping[str, Any],
    facts: Sequence[Mapping[str, str]],
    patient_context: Mapping[str, Any],
    prescription_context: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    dose_facts = [
        fact for fact in facts
        if fact.get("fact_type") == "dosage"
        and _fact_matches_patient_group(fact, patient_context)
        and _dose_fact_matches_prescription_context(fact, prescription_context)
    ]
    if not dose_facts:
        return None
    try:
        administrations = float(medicine.get("administrations_per_day"))
    except (TypeError, ValueError):
        administrations = 0
    try:
        per_admin_mg = float(medicine.get("calculated_primary_max_per_administration_mg"))
    except (TypeError, ValueError):
        per_admin_mg = 0
    daily_value = medicine.get("calculated_primary_daily_dose_mg")
    if daily_value is None:
        daily_value = medicine.get("calculated_daily_dose_mg")
    try:
        daily_mg = float(daily_value)
    except (TypeError, ValueError):
        daily_mg = 0
    for fact in dose_facts:
        text = fact["text"]
        if _is_complex_regimen_fact(text):
            # A phased or alternative course must be evaluated as a complete
            # regimen.  Do not flatten one phase into a global daily limit.
            # Explicit maxima remain covered by ``_dose_review_candidate``.
            continue
        expected_times, expected_interval = _expected_frequency_detail(text)
        per_dose_values = _per_dose_amounts(text)
        if per_dose_values and expected_times and daily_mg:
            expected_per_dose = max(per_dose_values)
            expected_daily = expected_per_dose * expected_times
            if daily_mg > expected_daily:
                candidate = _dose_candidate(
                    medicine,
                    fact,
                    "dose_exceeds_usual_or_max_candidate",
                    (
                        f"Calculated daily dose {daily_mg:g} mg/day exceeds "
                        f"RAG-supported regimen-derived daily amount {expected_daily:g} mg/day "
                        f"({expected_per_dose:g} mg per dose, "
                        f"{expected_interval or f'{expected_times:g} administrations/day'})."
                    ),
                )
                candidate["rag_usual_daily_dose_mg"] = expected_daily
                candidate["rag_per_dose_mg"] = expected_per_dose
                candidate["rag_expected_administrations_per_day"] = expected_times
                return candidate
        if expected_times and administrations and administrations != expected_times:
            if not _has_strict_frequency_requirement(text):
                continue
            candidate = _dose_candidate(
                medicine,
                fact,
                "frequency_pattern_mismatch_candidate",
                (
                    f"Prescription frequency pattern gives {administrations:g} administration(s)/day "
                    f"({_text(medicine.get('interval_pattern', 'unknown')) or 'unknown interval'}), "
                    f"but RAG-supported regimen evidence indicates about {expected_times:g} administration(s)/day "
                    f"({expected_interval})."
                ),
            )
            candidate["rag_expected_administrations_per_day"] = expected_times
            candidate["rag_expected_interval_pattern"] = expected_interval
            return candidate
        if per_dose_values and per_admin_mg and per_admin_mg > max(per_dose_values):
            expected_per_dose = max(per_dose_values)
            candidate = _dose_candidate(
                medicine,
                fact,
                "per_dose_amount_mismatch_candidate",
                (
                    f"Prescription primary-component amount per administration is {per_admin_mg:g} mg, "
                    f"but RAG-supported per-dose evidence mentions {expected_per_dose:g} mg."
                ),
            )
            candidate["rag_per_dose_mg"] = expected_per_dose
            candidate["calculated_primary_per_administration_mg"] = per_admin_mg
            return candidate
    return None


_CHILD_AGE_RANGE = re.compile(
    r"\bchildren?\s*(?P<min>\d+(?:\.\d+)?)\s*(?:-|to)\s*(?P<max>\d+(?:\.\d+)?)\s*years?\b|"
    r"\b(?P<min2>\d+(?:\.\d+)?)\s*(?:-|to)\s*(?P<max2>\d+(?:\.\d+)?)\s*years?\b",
    re.IGNORECASE,
)
_MG_RANGE = re.compile(
    r"(?P<min>\d+(?:\.\d+)?)\s*(?:-|to)\s*(?P<max>\d+(?:\.\d+)?)\s*mg\b",
    re.IGNORECASE,
)


def _weight_kg(patient_context: Mapping[str, Any]) -> float | None:
    match = re.search(r"\b(weight|wt)\b.{0,20}?(\d+(?:\.\d+)?)\s*kg\b", _text(patient_context), re.IGNORECASE)
    return float(match.group(2)) if match else None


def _pediatric_dose_candidate(
    medicine: Mapping[str, Any],
    facts: Sequence[Mapping[str, str]],
    patient_context: Mapping[str, Any],
) -> dict[str, Any] | None:
    if not patient_context.get("is_child"):
        return None
    age = patient_context.get("age_years")
    try:
        age_years = float(age)
    except (TypeError, ValueError):
        age_years = None
    strength = medicine.get("strength_mg")
    daily = medicine.get("calculated_daily_dose_mg")
    try:
        strength_mg = float(strength)
    except (TypeError, ValueError):
        strength_mg = None
    try:
        daily_mg = float(daily)
    except (TypeError, ValueError):
        daily_mg = None
    if strength_mg is None and daily_mg is None:
        return None
    for fact in facts:
        if fact.get("fact_type") != "dosage":
            continue
        text = fact["text"]
        if not re.search(r"\b(child|children|pediatric|paediatric)\b", text, re.IGNORECASE):
            continue
        age_match = _CHILD_AGE_RANGE.search(text)
        if age_match and age_years is not None:
            min_age = float(age_match.group("min") or age_match.group("min2"))
            max_age = float(age_match.group("max") or age_match.group("max2"))
            if not (min_age <= age_years <= max_age):
                continue
        mg_match = _MG_RANGE.search(text)
        expected_frequency = _expected_frequency_per_day(text)
        if mg_match and strength_mg is not None:
            max_per_dose = float(mg_match.group("max"))
            max_daily = max_per_dose * expected_frequency if expected_frequency else None
            if strength_mg > max_per_dose or (max_daily and daily_mg and daily_mg > max_daily):
                result = _dose_candidate(
                    medicine,
                    fact,
                    "pediatric_safety_candidate",
                    "Child age context and RAG pediatric dose evidence suggest the prescribed per-dose or daily amount needs review.",
                )
                result.update(
                    {
                        "patient_risk_type": "pediatric",
                        "patient_evidence": _text(patient_context.get("context_evidence", {}).get("is_child", "")) or f"Age {age_years:g}" if age_years is not None else "child",
                        "medicine_fact_evidence": text,
                        "rag_pediatric_max_per_dose_mg": max_per_dose,
                        "rag_pediatric_max_daily_dose_mg": max_daily,
                    }
                )
                return result
    return None


def _duration_days(raw_text: Any) -> float | None:
    match = re.search(
        r"-\s*\(\s*(\d+(?:\.\d+)?)\s*(Day|Days|Week|Weeks|Month|Months)\(s\)?\s*\)",
        _text(raw_text),
        re.IGNORECASE,
    )
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2).casefold()
    if unit.startswith("week"):
        return value * 7
    if unit.startswith("month"):
        return value * 30
    return value


def _expected_frequency_per_day(text: str) -> float | None:
    values = [value for pattern, value in _FREQUENCY_FACT_PATTERNS if pattern.search(text)]
    return max(values) if values else None


def _patient_risks(
    patient_context: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    return [
        risk
        for risk in patient_context.get("patient_risks", []) or []
        if isinstance(risk, Mapping)
        and _text(risk.get("risk_type", ""))
        and _text(risk.get("evidence", ""))
    ]


def _risk_action(text: str) -> bool:
    return bool(
        _SAFETY_ACTION_TERMS.search(text)
        and not _NEGATED_RISK_TERMS.search(text)
    )


def _linked_risk_action(text: str, risk_terms: re.Pattern[str]) -> bool:
    """Require a risk term and safety action in the same local clause."""
    return any(
        risk_terms.search(clause) and _risk_action(clause)
        for clause in re.split(r"[.!?;\n]+", text)
        if clause.strip()
    )


def _direct_population_precaution(
    text: str,
    risk_terms: re.Pattern[str],
) -> bool:
    """Return whether a precaution directly governs the current medicine.

    Precaution fields sometimes describe an interaction and then state that the
    *other* medicine is contraindicated in pregnancy.  Only imperative/direct
    population warnings are promoted here; ambiguous narrative remains for
    interaction handling instead of being misattributed to the current drug.
    """
    direct_start = re.compile(
        r"^\s*(?:"
        r"(?:pregnan(?:t|cy)|gestation|trimester|lactat(?:ing|ion)|"
        r"breast[ -]?feed(?:ing)?|nursing)\s*[:\-]?\s*|"
        r"(?:avoid|do\s+not\s+use|not\s+recommended|contraindicat\w*|"
        r"use\s+(?:only|with\s+caution)|caution)\b"
        r")",
        re.IGNORECASE,
    )
    return any(
        risk_terms.search(clause)
        and _risk_action(clause)
        and direct_start.search(clause)
        for clause in re.split(r"[.!?;\n]+", text)
        if clause.strip()
    )


def _substance_variants(value: Any) -> set[str]:
    substance = _text(value).casefold()
    if not substance:
        return set()
    variants = {substance}
    if substance.endswith("s"):
        variants.add(substance[:-1])
    else:
        variants.add(substance + "s")
    return variants


def _allergy_fact_matches(
    risk: Mapping[str, Any],
    fact: Mapping[str, str],
    medicine: Mapping[str, Any],
    all_facts: Sequence[Mapping[str, str]],
) -> bool:
    if fact["fact_type"] not in {"contraindication", "precaution"}:
        return False
    text = fact["text"]
    if not _ALLERGY_FACT_TERMS.search(text):
        return False
    variants = _substance_variants(risk.get("substance", ""))
    if not variants or variants & {"drug", "drugs"}:
        return False
    generic_name = _text(medicine.get("generic_name", ""))
    class_text = " ".join(
        item["text"]
        for item in all_facts
        if item["fact_type"] == "drug_class"
    )
    relationship_text = " ".join((text, generic_name, class_text))
    return any(_contains_phrase(relationship_text, term) for term in variants)


def _risk_fact_matches(
    risk: Mapping[str, Any],
    fact: Mapping[str, str],
    medicine: Mapping[str, Any],
    all_facts: Sequence[Mapping[str, str]],
) -> str | None:
    risk_type = _text(risk.get("risk_type", "")).casefold()
    fact_type = fact["fact_type"]
    text = fact["text"]
    if (
        risk_type == "renal_impairment"
        and fact_type in {"dosage", "contraindication", "precaution"}
        and _linked_risk_action(text, _RENAL_TERMS)
    ):
        return "renal_risk_candidate"
    if (
        risk_type == "hepatic_impairment"
        and fact_type in {"dosage", "contraindication", "precaution"}
        and _linked_risk_action(text, _HEPATIC_TERMS)
    ):
        return "hepatic_risk_candidate"
    if (
        risk_type == "pregnancy"
        and fact_type in {"pregnancy_lactation", "contraindication", "precaution"}
        and (
            _linked_risk_action(text, _PREGNANCY_TERMS)
            if fact_type != "precaution"
            else _direct_population_precaution(text, _PREGNANCY_TERMS)
        )
    ):
        return "pregnancy_safety_candidate"
    if (
        risk_type == "lactation"
        and fact_type in {"pregnancy_lactation", "contraindication", "precaution"}
        and (
            _linked_risk_action(text, _LACTATION_TERMS)
            if fact_type != "precaution"
            else _direct_population_precaution(text, _LACTATION_TERMS)
        )
    ):
        return "lactation_safety_candidate"
    if (
        risk_type == "pediatric"
        and fact_type in {"pediatric_usage", "contraindication", "precaution"}
        and _linked_risk_action(text, _PEDIATRIC_CONTEXT_TERMS)
    ):
        return "pediatric_safety_candidate"
    if (
        risk_type == "elderly"
        and fact_type in {"dosage", "contraindication", "precaution", "pediatric_usage"}
        and _linked_risk_action(text, _ELDERLY_TERMS)
    ):
        return "elderly_safety_candidate"
    if (
        risk_type == "allergy_hypersensitivity"
        and _allergy_fact_matches(risk, fact, medicine, all_facts)
    ):
        return "allergy_contraindication_candidate"
    if (
        risk_type == "hyperkalemia"
        and fact_type in {"contraindication", "precaution", "interaction", "side_effects"}
        and re.search(r"\b(hyperkala?emia|potassium)\b", text, re.IGNORECASE)
        and _risk_action(text)
    ):
        return "hyperkalemia_potassium_raising_drug_candidate"
    if (
        risk_type == "renal_monitoring_missing"
        and fact_type in {"dosage", "interaction", "precaution"}
        and re.search(r"\b(renal|creatinine clearance|nephrotoxic|kidney)\b", text, re.IGNORECASE)
        and re.search(r"\b(adjust|increase|avoid|monitor|nephrotoxic|clearance)\b", text, re.IGNORECASE)
    ):
        return "renal_monitoring_missing_candidate"
    return None


def _class_terms_for_generic(facts: Sequence[Mapping[str, str]]) -> set[str]:
    class_text = " ".join(
        fact["text"] for fact in facts if fact["fact_type"] == "drug_class"
    ).casefold()
    terms = {
        term
        for term in _CLASS_DUPLICATION_TERMS
        if _contains_phrase(class_text, term)
    }
    # "Non-opioid" contains the token "opioid" but is the opposite class.
    if re.search(r"\bnon[ -]?opioid\b", class_text, re.IGNORECASE):
        terms.discard("opioid")
    return terms


_INDICATION_STOPWORDS = {
    "acid", "associated", "caused", "chronic", "disease", "diseases", "drug",
    "following", "general", "indicated", "infection", "infections", "long",
    "management", "patients", "prophylaxis", "related", "severe", "syndrome",
    "treatment", "used", "with",
}


def _indication_terms(facts: Sequence[Mapping[str, str]]) -> set[str]:
    terms: set[str] = set()
    for fact in facts:
        if fact.get("fact_type") != "indication":
            continue
        for token in re.findall(r"[a-z][a-z-]{3,}", fact["text"].casefold()):
            token = token.strip("-")
            if token not in _INDICATION_STOPWORDS:
                terms.add(token)
    return terms


def _indication_gap_candidate(
    medicine: Mapping[str, Any],
    facts: Sequence[Mapping[str, str]],
    patient_context: Mapping[str, Any],
    prescription_context: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    # Broad antibiotic appropriateness is intentionally not generated from
    # medicine monograph indication text here. It should come from guideline RAG
    # facts that explicitly link the patient's condition to recommended,
    # avoided, or unnecessary antimicrobial treatment.
    return None

def _therapeutic_duplication_candidates(
    medicines: Sequence[Mapping[str, Any]],
    facts_by_id: Mapping[str, Sequence[Mapping[str, str]]],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    by_generic: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for medicine in medicines:
        generic_id = _text(medicine.get("generic_id", ""))
        if generic_id:
            by_generic[generic_id].append(medicine)
    for generic_id, meds in by_generic.items():
        if len(meds) < 2:
            continue
        generic_name = _text(meds[0].get("generic_name", ""))
        evidence = "; ".join(_text(med.get("raw_text", "")) for med in meds)
        candidates.append(
            {
                "section": "Rx",
                "rule_name": "therapeutic_duplication_candidate",
                "generic_id": generic_id,
                "generic_name": generic_name,
                "severity": "needs_review",
                "trigger_source": "medicine_mapping",
                "why_candidate": "Multiple current prescription items map to the same active ingredient.",
                "evidence": evidence,
                "source_field": "current medications",
                "needs_llm_confirmation": True,
                "medicine_fact_evidence": "",
                "duplicate_class": "same active ingredient",
            }
        )

    by_class: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    class_fact_by_id: dict[str, str] = {}
    for medicine in medicines:
        generic_id = _text(medicine.get("generic_id", ""))
        if not generic_id:
            continue
        facts = facts_by_id.get(generic_id, [])
        for term in _class_terms_for_generic(facts):
            by_class[term].append(medicine)
            class_fact_by_id[generic_id] = _join_unique(
                [
                    fact["text"]
                    for fact in facts
                    if fact["fact_type"] == "drug_class" and _contains_phrase(fact["text"], term)
                ]
            )
    for term, meds in by_class.items():
        unique_ids = {_text(med.get("generic_id", "")) for med in meds}
        if len(unique_ids) < 2:
            continue
        evidence = "; ".join(_text(med.get("raw_text", "")) for med in meds)
        fact_text = " | ".join(class_fact_by_id.get(_text(med.get("generic_id", "")), "") for med in meds)
        candidates.append(
            {
                "section": "Rx",
                "rule_name": "therapeutic_duplication_candidate",
                "generic_id": "",
                "generic_name": "",
                "severity": "needs_review",
                "trigger_source": "medicine_fact",
                "why_candidate": f"Multiple current medicines have RAG drug-class evidence for the same class: {term}.",
                "evidence": f"{evidence} | Medicine facts: {fact_text}",
                "source_field": "drug class",
                "needs_llm_confirmation": True,
                "medicine_fact_evidence": fact_text,
                "duplicate_class": term,
            }
        )
    return candidates


def _rag_food_candidate(
    medicine: Mapping[str, Any],
    fact: Mapping[str, str],
    advice_and_instructions: str,
) -> dict[str, Any] | None:
    if fact["fact_type"] not in {"interaction", "administration", "precaution"}:
        return None
    fact_text = fact["text"]
    fact_has_food = _FOOD_CONFLICT_TERMS.search(fact_text)
    rx_has_food = _FOOD_CONFLICT_TERMS.search(advice_and_instructions)
    # Milk/dairy is a clinically relevant calcium exposure for facts that
    # mention calcium-containing products, but this remains evidence-linked:
    # no fact about calcium/food, no candidate.
    if not fact_has_food or not rx_has_food:
        return None
    if not re.search(r"\b(avoid|separat\w*|reduce\w* absorption|decreas\w* absorption|impair\w* absorption|absorption .* impair\w*|do not take|interaction)\b", fact_text, re.IGNORECASE):
        return None
    result = _candidate(
        medicine,
        fact,
        "food_interaction_candidate",
        "Generic-specific RAG evidence mentions a food/dairy interaction and the prescription/advice mentions that food exposure.",
        section="Rx",
    )
    result.update(
        {
            "medicine_fact_evidence": fact_text,
            "evidence": (
                f"Medicine: {_text(medicine.get('raw_text', ''))} | "
                f"Advice/instruction: {advice_and_instructions} | Medicine fact: {fact_text}"
            ),
        }
    )
    return result


def _other_medicine_mentioned(
    interaction_text: str,
    current_id: str,
    medicines: Sequence[Mapping[str, Any]],
    facts_by_id: Mapping[str, Sequence[Mapping[str, str]]],
) -> str | None:
    for other in medicines:
        other_id = _text(other.get("generic_id", ""))
        other_name = _text(other.get("generic_name", ""))
        if not other_id or other_id == current_id or not other_name:
            continue
        if any(_contains_phrase(interaction_text, term) for term in _generic_match_terms(other_name)):
            return other_name
        other_classes = [
            fact["text"].casefold()
            for fact in facts_by_id.get(other_id, [])
            if fact["fact_type"] == "drug_class"
        ]
        class_keywords = {
            keyword
            for keyword in _STRONG_CLASS_KEYWORDS
            if any(_contains_phrase(class_text, keyword) for class_text in other_classes)
        }
        if any(_contains_phrase(interaction_text, keyword) for keyword in class_keywords):
            return other_name
    return None


def _administration_instruction(text: str) -> tuple[str, re.Pattern[str]] | None:
    # Permissive food statements describe flexibility, not a required timing
    # instruction.  They must never create a conflict with "before" or "after"
    # meal directions written by the prescriber.
    if re.search(
        r"\b(?:with\s+or\s+without\s+(?:food|meals?)|regardless\s+of\s+(?:food|meals?)|food\s+does\s+not\s+affect)\b",
        text,
        re.IGNORECASE,
    ):
        return None
    patterns = (
        (
            "medicine separation",
            re.compile(
                r"\b(separat\w*|do not take together|at least \d+(?:\.\d+)? (?:hours?|minutes?) (?:before|after))\b",
                re.IGNORECASE,
            ),
        ),
        ("empty-stomach timing", re.compile(r"\bempty stomach\b", re.IGNORECASE)),
        (
            "meal timing",
            re.compile(
                r"\b(before|after|with) (?:food|meals?|eating)\b",
                re.IGNORECASE,
            ),
        ),
        (
            "route instruction",
            re.compile(
                r"\b(apply topically|for inhalation|inhale|intramuscular|intravenous|iv|infusion|sublingual)\b",
                re.IGNORECASE,
            ),
        ),
    )
    for label, pattern in patterns:
        if pattern.search(text):
            return label, pattern
    return None


_INJECTION_ADMIN_TERMS = re.compile(
    r"\b(?:iv|intravenous|infusion|injection|injectable|vial|ampou?le|"
    r"reconstitut(?:e|ed|ion)|water for injection|dextrose for infusion|saline for infusion)\b",
    re.IGNORECASE,
)
_ORAL_FORM_TERMS = re.compile(
    r"\b(?:tablet|capsule|caplet|oral|sachet)\b", re.IGNORECASE
)
_INJECTION_FORM_TERMS = re.compile(
    r"\b(?:injection|injectable|infusion|iv|vial|ampou?le|powder for injection)\b",
    re.IGNORECASE,
)
_TOPICAL_ADMIN_TERMS = re.compile(
    r"\b(?:topical|apply|affected area|skin|cream|ointment|gel)\b",
    re.IGNORECASE,
)
_TOPICAL_FORM_TERMS = re.compile(r"\b(?:cream|ointment|gel|lotion)\b", re.IGNORECASE)


def _administration_fact_matches_medicine_form(
    medicine: Mapping[str, Any],
    fact_text: str,
) -> bool:
    """Avoid sending administration facts for a different route/form.

    This is not disease-specific tuning; it only checks whether the administration
    evidence belongs to the prescribed dosage form/route.
    """
    form_text = _text(
        " ".join(
            [
                _text(medicine.get("dosage_form", "")),
                _text(medicine.get("dosage_form_hint", "")),
                _text(medicine.get("raw_text", "")),
            ]
        )
    )
    if _INJECTION_ADMIN_TERMS.search(fact_text) and not _INJECTION_FORM_TERMS.search(form_text):
        return False
    if _TOPICAL_ADMIN_TERMS.search(fact_text) and not _TOPICAL_FORM_TERMS.search(form_text):
        return False
    if _ORAL_FORM_TERMS.search(form_text) and _INJECTION_ADMIN_TERMS.search(fact_text):
        return False
    return True


def generate_safety_candidates(
    medicines: Sequence[Mapping[str, Any]],
    medicine_facts: Sequence[Mapping[str, Any]],
    patient_context: Mapping[str, Any],
    raw_advice: str = "",
    prescription_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return conservative, evidence-backed candidates requiring confirmation."""
    candidates: list[dict[str, Any]] = []
    warnings: list[str] = []
    rag_gaps: list[dict[str, Any]] = []
    facts_by_id = _fact_index(medicine_facts)
    patient_risks = _patient_risks(patient_context)
    advice = _text(raw_advice)

    for fact_result in medicine_facts:
        for warning in fact_result.get("warnings", []):
            warnings.append(_text(warning))

    for medicine in medicines:
        generic_id = _text(medicine.get("generic_id", ""))
        generic_name = _text(medicine.get("generic_name", ""))
        if not generic_id:
            if medicine.get("known_supportive_item"):
                continue
            if _text(medicine.get("match_confidence", "")) == "inferred_needs_review":
                warnings.append(
                    f"medicine mapping needs review for {_text(medicine.get('raw_text', ''))!r}"
                )
                continue
            warnings.append(
                f"medicine {generic_name or _text(medicine.get('raw_text', ''))!r} has no generic_id"
            )
            continue
        strength_candidate = _strength_mismatch_candidate(medicine)
        if strength_candidate:
            candidates.append(strength_candidate)
        facts = facts_by_id.get(generic_id, [])
        if medicine.get("calculated_daily_dose_mg") is not None and not any(
            fact.get("fact_type") == "dosage" for fact in facts
        ):
            rag_gaps.append(
                {
                    "gap_type": "missing_dose_fact",
                    "generic_id": generic_id,
                    "generic_name": generic_name,
                    "evidence": _text(medicine.get("raw_text", "")),
                    "why_gap_matters": "Daily dose could be calculated, but no dosage fact was retrieved for this generic.",
                    "needs_rag_update": True,
                }
            )
        dose_candidate = _dose_review_candidate(medicine, facts, patient_context, prescription_context)
        if dose_candidate:
            candidates.append(dose_candidate)
        regimen_candidate = _rag_regimen_candidate(medicine, facts, patient_context, prescription_context)
        if regimen_candidate:
            candidates.append(regimen_candidate)
        pediatric_dose_candidate = _pediatric_dose_candidate(medicine, facts, patient_context)
        if pediatric_dose_candidate:
            candidates.append(pediatric_dose_candidate)
        indication_candidate = _indication_gap_candidate(
            medicine, facts, patient_context, prescription_context
        )
        if indication_candidate:
            candidates.append(indication_candidate)
        advice_and_instructions = translated_text(" ".join(
            (
                advice,
                _text(medicine.get("raw_text", "")),
                _text(medicine.get("instructions", "")),
                _text(medicine.get("additionalInstructions", "")),
            )
        ))
        for risk in patient_risks:
            before_count = len(candidates)
            for fact in facts:
                rule_name = _risk_fact_matches(risk, fact, medicine, facts)
                if rule_name:
                    if rule_name == "renal_monitoring_missing_candidate":
                        candidate = _risk_candidate(medicine, fact, risk, rule_name)
                        candidate["section"] = "Inv"
                        candidate["why_candidate"] = (
                            "Renal-function testing is ordered but no result is documented, "
                            "and generic-specific RAG evidence mentions renal/nephrotoxic dose or monitoring relevance."
                        )
                        candidates.append(candidate)
                    else:
                        candidates.append(
                            _risk_candidate(medicine, fact, risk, rule_name)
                        )
            risk_type = _text(risk.get("risk_type", ""))
            if len(candidates) == before_count and risk_type in {
                "renal_impairment",
                "hepatic_impairment",
                "pregnancy",
                "lactation",
                "allergy_hypersensitivity",
                "hyperkalemia",
                "renal_monitoring_missing",
            }:
                relevant_fact_seen = any(
                    (
                        risk_type == "renal_impairment"
                        and re.search(r"\b(renal|kidney|creatinine clearance|crcl|egfr)\b", fact["text"], re.IGNORECASE)
                    )
                    or (
                        risk_type == "hepatic_impairment"
                        and re.search(r"\b(hepatic|liver|jaundice|bilirubin|alt|ast)\b", fact["text"], re.IGNORECASE)
                    )
                    or (
                        risk_type == "pregnancy"
                        and re.search(r"\b(pregnan|foetal|fetal|trimester)\b", fact["text"], re.IGNORECASE)
                    )
                    or (
                        risk_type == "lactation"
                        and re.search(r"\b(lactat|breast ?feed|nursing)\b", fact["text"], re.IGNORECASE)
                    )
                    or (
                        risk_type == "allergy_hypersensitivity"
                        and re.search(r"\b(allerg|hypersensitiv)\b", fact["text"], re.IGNORECASE)
                    )
                    or (
                        risk_type == "hyperkalemia"
                        and re.search(r"\b(hyperkala?emia|potassium)\b", fact["text"], re.IGNORECASE)
                    )
                    or (
                        risk_type == "renal_monitoring_missing"
                        and re.search(r"\b(renal|kidney|nephrotoxic|creatinine clearance)\b", fact["text"], re.IGNORECASE)
                    )
                    for fact in facts
                )
                if not relevant_fact_seen:
                    rag_gaps.append(
                        {
                            "gap_type": f"missing_{risk_type}_safety_fact",
                            "generic_id": generic_id,
                            "generic_name": generic_name,
                            "evidence": _text(risk.get("evidence", "")),
                            "why_gap_matters": (
                                f"Patient risk {risk_type} exists, but no matching generic-specific RAG safety fact was retrieved."
                            ),
                            "needs_rag_update": True,
                        }
                    )
        for fact in facts:
            fact_type = fact["fact_type"]
            text = fact["text"]

            if fact_type == "interaction":
                other_name = _other_medicine_mentioned(
                    text, generic_id, medicines, facts_by_id
                )
                if other_name:
                    candidates.append(
                        _candidate(
                            medicine,
                            fact,
                            "ddi_candidate",
                            f"Interaction evidence explicitly references current medicine {other_name}.",
                            section="Rx",
                        )
                    )
                food_candidate = _rag_food_candidate(medicine, fact, advice_and_instructions)
                if food_candidate:
                    candidates.append(food_candidate)

            if fact_type == "administration":
                instruction = _administration_instruction(text)
                if (
                    instruction
                    and _administration_fact_matches_medicine_form(medicine, text)
                    and not instruction[1].search(advice_and_instructions)
                ):
                    candidates.append(
                        _candidate(
                            medicine,
                            fact,
                            "administration_advice_candidate",
                            f"Important {instruction[0]} appears in medicine evidence but not in Advice.",
                            section="Adv",
                        )
                    )

            if fact_type == "contraindication":
                matched_condition = next(
                    (
                        _text(condition.get("concept", ""))
                        for condition in patient_context.get("active_conditions", [])
                        if isinstance(condition, Mapping)
                        and _contains_phrase(text, _text(condition.get("concept", "")))
                    ),
                    "",
                )
                context_match = matched_condition
                if context_match:
                    candidates.append(
                        _candidate(
                            medicine,
                            fact,
                            "contraindication_candidate",
                            f"Contraindication evidence explicitly references active context: {context_match}.",
                            section="Rx",
                        )
                    )

    candidates.extend(_therapeutic_duplication_candidates(medicines, facts_by_id))

    return {
        "candidates": _consolidate_candidates(candidates),
        "warnings": list(dict.fromkeys(filter(None, warnings))),
        "rag_gaps": list({str(item): item for item in rag_gaps}.values()),
    }


__all__ = ["generate_safety_candidates"]
