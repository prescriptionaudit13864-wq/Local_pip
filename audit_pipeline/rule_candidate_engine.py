"""Create section-wise provisional audit candidates from structured evidence.

This module applies conservative deterministic rules and aggregates candidates
from upstream evidence engines. It does not create final audit findings.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping, Sequence

from .frequency_parser import normalize_digits
from .investigation_catalog import match_investigation
from .text_translation import translated_text


SECTIONS = ("Demo", "CC", "Hx", "Dx", "Inv", "Rx", "Adv")

_DEFAULT_RULE_IDS = {
    "age_invalid_candidate": "Demo.1",
    "gender_missing_candidate": "Demo.1",
    "male_pregnancy_context_candidate": "Demo.1",
    "duration_missing_candidate": "CC.3",
    "severity_missing_candidate": "CC.2",
    "site_or_source_missing_candidate": "CC.4",
    "complaint_text_unclear_candidate": "CC.term.unclear",
    "complaint_not_clinical_candidate": "CC.1",
    "complaint_clinical_evidence_inconsistency_candidate": "CC.hs",
    "complaint_demographic_context_candidate": "CC.context.demo",
    "complaint_temporal_plausibility_candidate": "CC.time.plausibility",
    "complaint_cross_context_consistency_candidate": "CC.context.conflict",
    "history_text_unclear_candidate": "Hx.term.unclear",
    "history_demographic_context_candidate": "Hx.context.demo",
    "history_detail_candidate": "Hx.detail",
    "history_temporal_plausibility_candidate": "Hx.time.plausibility",
    "history_context_consistency_candidate": "Hx.context.conflict",
    "history_duplicate_or_conflict_candidate": "Hx.duplicate",
    "diagnosis_missing_candidate": "Dx.cc.1",
    "diagnosis_not_supported_by_chief_complaint_candidate": "Dx.cc.1",
    "diagnosis_omits_significant_clinical_finding_candidate": "Dx.hs.2",
    "diagnosis_not_supported_by_clinical_findings_candidate": "Dx.hs.1",
    "diagnosis_text_unclear_candidate": "Dx.term.unclear",
    "diagnosis_demographic_context_candidate": "Dx.context.demo",
    "diagnosis_cross_context_consistency_candidate": "Dx.context.conflict",
    "extreme_vital_value_candidate": "Dx.vital.extreme",
    "medicine_timing_missing_candidate": "Rx.timing",
    "medicine_timing_mismatch_candidate": "Rx.timing",
    "medicine_route_or_instruction_incomplete_candidate": "Rx.instr",
    "frequency_missing_or_ambiguous_candidate": "Rx.freq.missing",
    "medicine_duration_missing_or_ambiguous_candidate": "Rx.duration.missing",
    "medicine_duration_mismatch_candidate": "Rx.duration.mismatch",
    "medicine_text_unclear_candidate": "Rx.term.unclear",
    "route_inconsistent_candidate": "Rx.route.mismatch",
    "dosage_form_inappropriate_candidate": "Rx.form.mismatch",
    "therapeutic_duplication_candidate": "Rx.dup.class",
    "indication_not_apparent_candidate": "Rx.indication.missing",
    "necessary_therapy_omitted_candidate": "Rx.omission",
    "renal_monitoring_missing_candidate": "Inv.monitor.renal",
    "food_interaction_candidate": "Rx.food",
    "hyperkalemia_potassium_raising_drug_candidate": "Rx.lab.hyperkalemia",
    "frequency_too_low_candidate": "Rx.freq.low",
    "frequency_pattern_mismatch_candidate": "Rx.freq.pattern",
    "per_dose_amount_mismatch_candidate": "Rx.dose.per_admin",
    "duration_too_long_candidate": "Rx.duration.long",
    "ddi_bleeding_risk_candidate": "Rx.ddi.bleeding",
    "nsaid_renal_risk_candidate": "Rx.risk.renal.nsaid",
    "pediatric_liquid_weight_dose_review_candidate": "Rx.dose.weight",
    "pregnancy_warning_candidate": "Rx.demo",
    "renal_risk_candidate": "Rx.risk.renal",
    "hepatic_risk_candidate": "Rx.risk.hepatic",
    "pregnancy_safety_candidate": "Rx.risk.pregnancy",
    "lactation_safety_candidate": "Rx.risk.lactation",
    "allergy_contraindication_candidate": "Rx.risk.allergy",
    "pediatric_safety_candidate": "Rx.risk.pediatric",
    "elderly_safety_candidate": "Rx.risk.elderly",
    "pediatric_warning_candidate": "Rx.demo",
    "ddi_candidate": "Rx.ddi",
    "contraindication_candidate": "Rx.cc.mis",
    "medicine_strength_unusual_or_mismatch_candidate": "Rx.strength.mismatch",
    "dose_exceeds_max_candidate": "Rx.dose.max",
    "dose_exceeds_usual_or_max_candidate": "Rx.dose.high",
    "high_daily_dose_review_candidate": "Rx.dose.review",
    "needs_weight_for_dose_review_candidate": "Rx.dose.weight",
    "administration_advice_candidate": "Adv.precau",
    "duplicate_active_prior_medicine_candidate": "Rx.followup.dup",
    "overlapping_same_generic_candidate": "Rx.followup.dup",
    "therapeutic_duplication_with_prior_candidate": "Rx.followup.class",
    "ddi_with_active_prior_medicine_candidate": "Rx.followup.ddi",
    "active_antibiotic_overlap_or_switch_candidate": "Rx.followup.abx",
    "redundant_investigation_candidate": "Inv.followup.redundant",
    "redundant_investigation_from_prior_result_candidate": "Inv.followup.redundant",
    "prior_ordered_test_result_followup_candidate": "Inv.followup.result",
    "unresolved_prior_abnormal_result_candidate": "Dx.followup",
    "followup_advice_candidate": "Adv.followup",
    "pregnancy_danger_sign_action_missing_candidate": "Adv.preg.danger",
    "guideline_advice_missing_candidate": "Adv.rag.missing",
    "advice_missing_candidate": "Adv.missing",
    "advice_content_unrecognized_candidate": "Adv.content.unrecognized",
    "advice_context_appropriateness_candidate": "Adv.context",
    "advice_followup_interval_candidate": "Adv.followup.interval",
    "guideline_avoid_medicine_candidate": "Rx.rag.avoid",
    "guideline_treatment_review_candidate": "Rx.rag.treatment",
    "guideline_investigation_missing_candidate": "Inv.rag.missing",
    "investigation_term_unrecognized_candidate": "Inv.term.unrecognized",
    "investigation_relevance_review_candidate": "Inv.relevance",
    "guideline_diagnosis_review_candidate": "Dx.rag.review",
}

_ADMINISTRATIVE_CC = re.compile(
    r"(?:came\s+for\s+)?(?:vaccination|vaccine|immuni[sz]ation|epi)(?:\s+(?:visit|only))?|"
    r"report\s+(?:collection|showing)|collect(?:ion)?\s+of\s+report|"
    r"follow[ -]?up(?:\s+only)?|(?:routine\s+)?(?:check[ -]?up|health\s+check[ -]?up)|"
    r"blood\s+group\s+test|pregnancy\s+registration|antenatal\s+registration|anc\s+registration",
    re.IGNORECASE,
)
_DURATION = re.compile(
    r"\b(?:for\s+|since\s+)?\d+(?:\.\d+)?\s*"
    r"(?:hours?|days?|weeks?|months?|years?)\b|"
    r"\b(today|yesterday|tonight|this\s+morning|this\s+evening|since\s+morning)\b",
    re.IGNORECASE,
)
_CONTACT_CONTEXT_CC = re.compile(
    r"\b(?:other\s+)?family\s+members?\b.*\b(?:itch(?:ing)?|symptoms?)\b|"
    r"\b(?:itch(?:ing)?|symptoms?)\b.*\b(?:other\s+)?family\s+members?\b|"
    r"\bhousehold\s+members?\b.*\b(?:itch(?:ing)?|symptoms?)\b|"
    r"\bcontact\s+history\b|"
    r"\bsimilar\s+symptoms?\s+in\s+family\b",
    re.IGNORECASE,
)
_PATIENT_OWN_SYMPTOM = re.compile(
    r"\b(patient|self|pt|he|she|boy|girl|mother|father)\b.*\b"
    r"(itch(?:ing)?|rash|lesion|pain|fever|cough|symptoms?)\b|"
    r"\bpatient\s+and\s+family\b|\bpatient\s+(?:with|has)\b",
    re.IGNORECASE,
)
_FOLLOWUP_CONTEXT_CC = re.compile(
    r"\b(?:improved|better|resolved|subsided|follow[ -]?up|report\s+(?:showing|review|collection))\b",
    re.IGNORECASE,
)
_MEAL_TIMING = re.compile(
    r"\b(?:before|after)\s+(?:the\s+)?(?:food|meals?|eating)\b|"
    r"\b(?:ac|pc)\b",
    re.IGNORECASE,
)
_NON_ORAL_FORM = re.compile(
    r"\b(topical|cream|ointment|gel|nasal|inhal|injection|infusion|"
    r"suppository|eye drop|ear drop|spray)\b",
    re.IGNORECASE,
)
_SPECIAL_TIMING_EXEMPT = re.compile(
    r"\b(?:ors|oral rehydration salts?|medical compressed oxygen|oxygen)\b",
    re.IGNORECASE,
)
_MEAL_TIMING_REQUIRED_FACT = re.compile(
    r"\b(empty\s+stomach|before\s+(?:food|meals?|eating)|after\s+(?:food|meals?|eating)|"
    r"with\s+(?:food|meals?)|take\s+with\s+food|food\s+(?:interaction|reduces?|increases?|affects?)|"
    r"meal\s+timing|separate\s+from\s+(?:food|meals?|dairy|milk|antacids?))\b",
    re.IGNORECASE,
)
_ROUTE_FORM = re.compile(
    r"\b(cream|ointment|gel|nasal|inhaler|inhalation|spray|drops?|"
    r"suppository|vaginal|pessary|injection|injectable|infusion)\b",
    re.IGNORECASE,
)
_ROUTE_INSTRUCTION = re.compile(
    r"\b(apply|topical(?:ly)?|instill|inhale|puffs?|nostrils?|nasal|"
    r"eyes?|ears?|vaginal|rectal|intravenous|intramuscular|subcutaneous|"
    r"external\s+use)\b",
    re.IGNORECASE,
)
_PREGNANCY_CONTEXT = re.compile(
    r"\b(pregnan\w*|gravida|gestation|antenatal|anc|trimester|"
    r"\d+\s*(?:weeks?|wks?)\b)",
    re.IGNORECASE,
)
_PREGNANCY_DANGER_SIGN = re.compile(
    r"\b(?:less|reduced|decreased|no)\s+fetal\s+movement\b|"
    r"\bbleeding\s+during\s+pregnancy\b|"
    r"\bper\s+vaginal\s+bleeding\b|"
    r"\bsevere\s+abdominal\s+pain\s+in\s+pregnancy\b|"
    r"\bconvulsion\s+in\s+pregnancy\b|"
    r"\bsevere\s+headache\s+with\s+pregnancy\b|"
    r"\bblurred\s+vision\s+with\s+pregnancy\b|"
    r"\bhigh\s+blood\s+pressure\s+in\s+pregnancy\b|"
    r"\bedema\s+with\s+pregnancy\b",
    re.IGNORECASE,
)
_HIGH_BP = re.compile(
    r"\b(?:high|raised|elevated)\s+(?:blood\s+pressure|bp)\b|"
    r"\bbp\s*(?:high|raised|elevated)\b|"
    r"\bbp\s*[:= -]*\s*(?:1[4-9]\d|[2-9]\d\d)\s*/\s*(?:9\d|[1-9]\d\d)\b",
    re.IGNORECASE,
)
_DANGER_ADVICE_COVERAGE = re.compile(
    r"\b(refer(?:ral)?|urgent|emergency|hospital|admit|obgyn|obs\s*&?\s*gyn|"
    r"obstetric|gyna?ecolog(?:y|ist)|specialist|follow[ -]?up|danger\s+sign|"
    r"return\s+(?:if|immediately)|fetal\s+movement\s+(?:monitor|count))\b",
    re.IGNORECASE,
)
_ORAL_WORD = re.compile(r"\b(oral|mouth|po|take by mouth)\b", re.IGNORECASE)
_INJECTION_WORD = re.compile(r"\b(injection|injectable|iv|im|intravenous|intramuscular)\b", re.IGNORECASE)
_DYSPHAGIA = re.compile(r"\b(dysphagia|difficulty swallowing|cannot swallow|swallowing tablets?)\b", re.IGNORECASE)
_SOLID_FORM = re.compile(r"\b(tablet|capsule|caplet)\b", re.IGNORECASE)
_COURSE_DURATION = re.compile(r"-\s*\(\s*(\d+(?:\.\d+)?)\s*(Day|Days|Week|Weeks|Month|Months)\(s\)?\s*\)", re.IGNORECASE)
_ONGOING_DURATION = re.compile(r"-\s*\(\s*(?:continue|ongoing|until\s+further\s+(?:advice|notice))\s*\)", re.IGNORECASE)


def _dosage_form_route(value: str) -> str:
    text = _text(value).casefold()
    # Specific route-qualified forms must precede generic words such as drops,
    # ointment, solution, and spray.
    if re.search(r"\b(ophthalmic|eye\s*(?:drops?|ointment|gel))\b", text):
        return "eye"
    if re.search(r"\b(otic|ear\s*drops?)\b", text):
        return "ear"
    if re.search(r"\b(nasal|nose\s*(?:drops?|spray))\b", text):
        return "nasal"
    if re.search(r"\b(inhaler|inhalation|nebulizer|nebuliser)\b", text):
        return "inhaled"
    if re.search(r"\b(vaginal|pessary)\b", text):
        return "vaginal"
    if re.search(r"\b(rectal|suppository)\b", text):
        return "rectal"
    if re.search(r"\b(injection|injectable|infusion|ampoule|vial)\b", text):
        return "parenteral"
    if re.search(r"\b(cream|ointment|gel|lotion|topical)\b", text):
        return "topical"
    if re.search(r"\b(tablet|capsule|caplet|syrup|suspension|oral\s+solution)\b", text):
        return "oral"
    return ""


def _instruction_route(value: str) -> str:
    text = _text(translated_text(value)).casefold()
    if re.search(r"\b(instill\s+in\s+(?:the\s+)?eye|eyes?|ophthalmic)\b", text):
        return "eye"
    if re.search(r"\b(instill\s+in\s+(?:the\s+)?ear|ears?|otic)\b", text):
        return "ear"
    if re.search(r"\b(nasal|nostril|instill\s+in\s+(?:the\s+)?nose)\b", text):
        return "nasal"
    if re.search(r"\b(inhale|inhaled|puffs?)\b", text):
        return "inhaled"
    if re.search(r"\b(vaginal|intravaginal)\b", text):
        return "vaginal"
    if re.search(r"\b(rectal|per\s+rectum)\b", text):
        return "rectal"
    if re.search(r"\b(intravenous|intramuscular|subcutaneous|parenteral)\b", text):
        return "parenteral"
    if re.search(r"\b(apply\s+topically|topical|external\s+use|affected\s+area)\b", text):
        return "topical"
    if re.search(r"\b(take\s+by\s+mouth|oral|chew\s+by\s+mouth|sublingual)\b", text):
        return "oral"
    return ""


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _field(record: Mapping[str, Any], *names: str) -> str:
    lowered = {str(key).strip().casefold(): value for key, value in record.items()}
    for name in names:
        value = _text(lowered.get(name.casefold(), ""))
        if value and value.casefold() not in {"nan", "none", "null"}:
            return value
    return ""


def _investigation_items(value: Any) -> list[str]:
    """Split writer-entered investigations without interpreting validity.

    PrescribeX stores separate UI rows as comma-separated text in the canonical
    row.  Each item must become its own provisional candidate so that one
    relevant test cannot mask an unrelated test in the same order set.
    """
    text = str(value or "").strip()
    if not text:
        return []
    items = [
        _text(part).strip(" .;,")
        for part in re.split(r"[\n;,]+", text)
    ]
    return [item for item in items if item and item.casefold() not in {
        "n/a", "na", "none", "nil", "-", "—",
    }]


def _is_administrative_complaint(raw: Any) -> bool:
    text = _text(translated_text(raw)).strip(" .:-")
    return _ADMINISTRATIVE_CC.fullmatch(text) is not None


def _is_contact_context_only_complaint(raw: Any) -> bool:
    text = _text(translated_text(raw)).strip(" .:-")
    if not text:
        return False
    return bool(_CONTACT_CONTEXT_CC.search(text) and not _PATIENT_OWN_SYMPTOM.search(text))


def _is_followup_context_only_complaint(raw: Any) -> bool:
    text = _text(translated_text(raw)).strip(" .:-")
    if not text:
        return False
    return bool(_FOLLOWUP_CONTEXT_CC.search(text))


def _is_pregnancy_context_only_complaint(raw: Any) -> bool:
    text = _text(translated_text(raw)).strip(" .:-")
    if not text:
        return False
    return bool(_PREGNANCY_CONTEXT.search(text)) and not re.search(
        r"\b(?:pain|ache|bleeding|headache|dizziness|fever|vomiting|weakness)\b",
        text,
        re.IGNORECASE,
    )


def _has_pregnancy_context(
    prescription: Mapping[str, Any],
    patient_context: Mapping[str, Any],
) -> bool:
    if patient_context.get("pregnancy_conflict"):
        return False
    if patient_context.get("is_pregnant"):
        return True
    combined = " ".join(
        _field(
            prescription,
            name,
        )
        for name in (
            "Chief_Complaint",
            "Chief Complaint",
            "Chief Complain",
            "Diagnosis",
            "ANC_Notes",
            "ANC/PNC Notes",
            "Health_Screening",
            "Health Screening",
        )
    )
    return _PREGNANCY_CONTEXT.search(combined) is not None


def _pregnancy_danger_evidence(prescription: Mapping[str, Any]) -> str:
    fields = [
        ("Chief Complaint", _field(prescription, "Chief_Complaint", "Chief Complaint", "Chief Complain")),
        ("Diagnosis", _field(prescription, "Diagnosis")),
        ("Health Screening", _field(prescription, "Health_Screening", "Health Screening")),
        ("On Examination", _field(prescription, "On_Examination", "On Examination")),
        ("Systemic Examination", _field(prescription, "Systemic_Examination", "Systemic Examination")),
    ]
    evidence: list[str] = []
    danger_found = False
    health_screening = ""
    for label, value in fields:
        if not value:
            continue
        if _PREGNANCY_DANGER_SIGN.search(value):
            danger_found = True
            evidence.append(f"{label}: {value}")
        elif label == "Health Screening" and _HIGH_BP.search(value):
            evidence.append(f"{label}: {value}")
        if label == "Health Screening":
            health_screening = value
    if danger_found and health_screening and re.search(r"\b(bp|pulse|heart\s*rate|hr)\b", health_screening, re.I):
        evidence.append(f"Health Screening: {health_screening}")
    return " | ".join(dict.fromkeys(evidence))


def _advice_has_danger_coverage(advice: str) -> bool:
    return bool(advice and _DANGER_ADVICE_COVERAGE.search(advice))


def _duration_days(raw: str) -> float | None:
    match = _COURSE_DURATION.search(raw)
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2).casefold()
    if unit.startswith("week"):
        return value * 7
    if unit.startswith("month"):
        return value * 30
    return value


def _has_ongoing_duration(raw: str) -> bool:
    return _ONGOING_DURATION.search(raw) is not None


def _explicit_guideline_duration_range(fact: Mapping[str, Any]) -> tuple[float, float] | None:
    """Return one unambiguous evidence duration in days, otherwise abstain."""
    text = " ".join(
        _text(fact.get(key, ""))
        for key in ("duration", "dose_or_duration", "fact_text")
    )
    matches = re.findall(
        r"(?:for\s+|duration\s*(?:of|:)?\s*)?"
        r"(\d+(?:\.\d+)?)\s*(?:-|–|to)?\s*(\d+(?:\.\d+)?)?\s*"
        r"(days?|weeks?|months?)\b|"
        r"\b(\d+(?:\.\d+)?)\s*[- ](day|week|month)\s*(?:course|treatment)",
        text,
        re.IGNORECASE,
    )
    ranges: set[tuple[float, float]] = set()
    for low, high, unit, single, single_unit in matches:
        if single:
            low = high = single
            unit = single_unit
        minimum = float(low)
        maximum = float(high or low)
        multiplier = 7.0 if unit.casefold().startswith("week") else 30.0 if unit.casefold().startswith("month") else 1.0
        ranges.add((minimum * multiplier, maximum * multiplier))
    return next(iter(ranges)) if len(ranges) == 1 else None


def _instruction_text_for_rule(medicine: Mapping[str, Any]) -> str:
    return _text(f"{medicine.get('raw_text', '')} {_structured_instruction_text(medicine)}")


def _structured_instruction_text(medicine: Mapping[str, Any]) -> str:
    values: list[str] = []

    def collect(payload: Mapping[str, Any]) -> None:
        for key, value in payload.items():
            if "instruction" not in str(key).casefold() or value is None:
                continue
            text = _text(value)
            if text and text.casefold() not in {"none", "null", "nan"}:
                values.append(text)

    collect(medicine)
    raw = _text(medicine.get("raw_text", ""))
    decoder = json.JSONDecoder()
    for index, character in enumerate(raw):
        if character != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(raw[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, Mapping):
            collect(payload)
    return _text(" ".join(values))


def _has_meal_timing(medicine: Mapping[str, Any]) -> bool:
    raw = _text(medicine.get("raw_text", ""))
    raw_without_json = re.sub(r"\{[^{}]*\}", " ", raw)
    timing_text = _text(
        translated_text(f"{raw_without_json} {_structured_instruction_text(medicine)}")
    )
    return _MEAL_TIMING.search(timing_text) is not None


def _cfg(protocol: Any, key: str, default: Any) -> Any:
    if isinstance(protocol, Mapping):
        return protocol.get(key, default)
    return getattr(protocol, key, default) if protocol is not None else default


def _timing_rule_exempt(medicine: Mapping[str, Any], protocol: Any) -> bool:
    raw = _text(medicine.get("raw_text", ""))
    required = _cfg(protocol, "meal_timing_required_items", ())
    if isinstance(required, str):
        required = (required,)
    for item in required or ():
        term = _text(item)
        if term and re.search(rf"(?<!\w){re.escape(term)}(?!\w)", raw, re.IGNORECASE):
            return False
    if medicine.get("requires_meal_timing") is False:
        return True
    return _SPECIAL_TIMING_EXEMPT.search(raw) is not None


def _medicine_fact_index(medicine_facts: Sequence[Mapping[str, Any]] | None) -> dict[str, list[Mapping[str, Any]]]:
    index: dict[str, list[Mapping[str, Any]]] = {}
    for result in medicine_facts or []:
        generic_id = _text(result.get("generic_id", ""))
        if not generic_id:
            continue
        facts = result.get("facts", [])
        if isinstance(facts, Sequence) and not isinstance(facts, (str, bytes)):
            index.setdefault(generic_id, []).extend(
                fact for fact in facts if isinstance(fact, Mapping)
            )
    return index


def _meal_timing_required_by_evidence(
    medicine: Mapping[str, Any],
    facts_by_id: Mapping[str, Sequence[Mapping[str, Any]]],
    protocol: Any,
) -> bool:
    identity = _text(
        " ".join(
            _text(medicine.get(key, ""))
            for key in ("raw_text", "brand_name", "generic_name")
        )
    )
    required = _cfg(protocol, "meal_timing_required_items", ())
    if isinstance(required, str):
        required = (required,)
    for item in required or ():
        term = _text(item)
        if term and re.search(rf"(?<!\w){re.escape(term)}(?!\w)", identity, re.IGNORECASE):
            return True
    if medicine.get("requires_meal_timing") is True:
        return True
    generic_id = _text(medicine.get("generic_id", ""))
    for fact in facts_by_id.get(generic_id, []):
        fact_type = _text(fact.get("fact_type", "")).casefold()
        if fact_type not in {"administration", "interaction", "precaution", "dosage"}:
            continue
        text = _text(translated_text(" ".join(_text(fact.get(key, "")) for key in ("text", "fact_text", "source_field"))))
        if _MEAL_TIMING_REQUIRED_FACT.search(text):
            return True
    return False


_QUANTIFIED_ENGLISH_MEAL_TIMING = re.compile(
    r"(?:at\s+least\s+)?(?P<amount>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>hours?|hrs?|minutes?|mins?)\s*"
    r"(?P<direction>before|after)(?:\s+(?:the\s+)?(?:meal|meals|food|eating))?",
    re.IGNORECASE,
)
_QUANTIFIED_BANGLA_MEAL_TIMING = re.compile(
    r"(?:আহারের|খাবারের|খাওয়ার|খাওয়ার)\s*"
    r"(?P<amount>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>মিনিট|ঘণ্টা|ঘন্টা)\s*"
    r"(?P<direction>আগে|পূর্বে|পর|পরে)",
    re.IGNORECASE,
)


def _timing_minutes(amount: str, unit: str) -> float:
    value = float(amount)
    normalized_unit = unit.casefold()
    is_hour = (
        normalized_unit.startswith(("hour", "hr"))
        or "ঘণ্ট" in unit
        or "ঘন্ট" in unit
    )
    return value * 60.0 if is_hour else value


def _quantified_meal_timings(text: Any) -> list[tuple[str, float]]:
    """Return explicit before/after-meal intervals in minutes."""
    normalized = normalize_digits(text)
    timings: list[tuple[str, float]] = []
    for match in _QUANTIFIED_ENGLISH_MEAL_TIMING.finditer(normalized):
        timings.append((
            match.group("direction").casefold(),
            _timing_minutes(match.group("amount"), match.group("unit")),
        ))
    for match in _QUANTIFIED_BANGLA_MEAL_TIMING.finditer(normalized):
        direction = "before" if match.group("direction") in {"আগে", "পূর্বে"} else "after"
        timings.append((
            direction,
            _timing_minutes(match.group("amount"), match.group("unit")),
        ))
    return timings


def _medicine_timing_mismatch(
    medicine: Mapping[str, Any],
    facts_by_id: Mapping[str, Sequence[Mapping[str, Any]]],
) -> tuple[str, str] | None:
    """Find an explicit meal interval shorter than a retrieved minimum."""
    prescribed_text = _instruction_text_for_rule(medicine)
    prescribed = _quantified_meal_timings(prescribed_text)
    if not prescribed:
        return None

    generic_id = _text(medicine.get("generic_id", ""))
    for fact in facts_by_id.get(generic_id, []):
        fact_type = _text(fact.get("fact_type", "")).casefold()
        if fact_type not in {"administration", "interaction", "precaution", "dosage"}:
            continue
        fact_text = _text(" ".join(
            _text(fact.get(key, "")) for key in ("text", "fact_text")
        ))
        required = _quantified_meal_timings(fact_text)
        if not required:
            continue
        minimum_by_direction: dict[str, float] = {}
        for direction, minutes in required:
            minimum_by_direction[direction] = max(
                minutes, minimum_by_direction.get(direction, 0.0)
            )
        for direction, minutes in prescribed:
            minimum = minimum_by_direction.get(direction)
            if minimum is not None and minutes < minimum:
                return prescribed_text, fact_text
    return None


def _rule_ids(protocol: Any) -> dict[str, str]:
    result = dict(_DEFAULT_RULE_IDS)
    overrides = _cfg(protocol, "candidate_rule_ids", {})
    if isinstance(overrides, Mapping):
        result.update({str(key): str(value) for key, value in overrides.items()})
    return result


def _candidate(
    section: str,
    rule_name: str,
    rule_ids: Mapping[str, str],
    why: str,
    evidence: str,
    *,
    trigger_source: str = "deterministic_rule",
    extras: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    evidence_strength = _candidate_evidence_strength(rule_name)
    result: dict[str, Any] = {
        "section": section,
        "rule_name": rule_name,
        "rule_id": rule_ids.get(rule_name, ""),
        "trigger_source": trigger_source,
        "why_candidate": why,
        "evidence": _text(evidence) or "Structured evidence triggered this candidate.",
        "needs_llm_confirmation": True,
        "rag_supported": trigger_source in {"safety_candidate_engine", "medicine_fact"},
        "evidence_strength": evidence_strength,
        "importance": _candidate_importance(rule_name, evidence_strength),
        "must_review_if_rejected": evidence_strength == "high",
    }
    if extras:
        for key, value in extras.items():
            if key not in result:
                result[key] = value
    return result


def _candidate_evidence_strength(rule_name: str) -> str:
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
            "renal_monitoring",
            "extreme_vital",
        )
    ):
        return "high"
    if any(
        marker in name
        for marker in (
            "medicine_timing",
            "frequency",
            "duration",
            "route",
            "form",
            "food",
            "pediatric",
            "strength",
            "diagnosis_omits",
        )
    ):
        return "medium"
    return "low"



def _candidate_importance(rule_name: str, evidence_strength: str) -> str:
    name = _text(rule_name).casefold()
    if any(term in name for term in ("danger", "emergency", "extreme_vital", "contraindication", "dose_exceeds_max", "allergy", "renal_risk", "hepatic_risk", "pregnancy_safety", "lactation_safety", "avoid_medicine")):
        return "critical_review"
    if evidence_strength == "high" or any(term in name for term in ("dose", "ddi", "investigation", "treatment", "diagnosis_not_supported")):
        return "moderate_review"
    if any(term in name for term in ("duration_missing", "severity_missing", "timing_missing", "advice_missing")):
        return "minor_documentation"
    return "review"

def _complaint_has_duration(item: Mapping[str, Any]) -> bool:
    if item.get("value") is not None and str(item.get("unit", "")).casefold() in {
        "hour", "hours", "day", "days", "week", "weeks", "month", "months", "year", "years"
    }:
        return True
    return _DURATION.search(translated_text(_text(item.get("raw_text", "")))) is not None


def _diagnosis_mentions(diagnosis: str, concept: str) -> bool:
    variants = {
        "anemia": ("anemia", "anaemia"),
        "anaemia": ("anemia", "anaemia"),
        "fever": ("fever", "febrile", "pyrexia"),
        "dehydration": ("dehydration", "dehydrated"),
    }.get(concept.casefold(), (concept.casefold(),))
    lowered = diagnosis.casefold()
    return any(re.search(rf"(?<!\w){re.escape(term)}(?!\w)", lowered) for term in variants)


_FEVER_EXPLAINING_DIAGNOSIS = re.compile(
    r"\b("
    r"infection|infective|sepsis|"
    r"uti|urinary\s+tract\s+infection|pyelonephritis|cystitis|"
    r"rti|urti|upper\s+respiratory\s+tract\s+infection|"
    r"lRTI|lower\s+respiratory\s+tract\s+infection|pneumonia|bronchitis|tonsillitis|"
    r"dengue|malaria|typhoid|enteric\s+fever|influenza|covid|viral|"
    r"abscess|cellulitis|wound\s+infection|otitis|sinusitis|meningitis|"
    r"gastroenteritis|diarrhoea|diarrhea|dysentery|"
    r"tb|tuberculosis|hepatitis|cholangitis|cholecystitis|"
    r"inflammat(?:ion|ory)"
    r")\b",
    re.IGNORECASE,
)


def _diagnosis_explains_significant_finding(diagnosis: str, concept: str) -> bool:
    if _diagnosis_mentions(diagnosis, concept):
        return True
    if concept.casefold() == "fever" and _FEVER_EXPLAINING_DIAGNOSIS.search(diagnosis):
        return True
    return False


def _explicit_diagnosis_contradictions(
    diagnosis: str, clinical_evidence: Sequence[Mapping[str, Any]]
) -> list[Mapping[str, Any]]:
    contradictions = []
    for item in clinical_evidence:
        status = _text(item.get("status", "")).casefold()
        concept = _text(item.get("concept", "")).strip(" .:-").casefold()
        if status in {"absent", "normal"} and concept and _diagnosis_mentions(diagnosis, concept):
            contradictions.append(item)
    return contradictions


def _import_candidate(
    upstream: Mapping[str, Any],
    rule_ids: Mapping[str, str],
    default_trigger: str,
) -> dict[str, Any] | None:
    section = _text(upstream.get("section", ""))
    rule_name = _text(upstream.get("rule_name", ""))
    evidence = _text(upstream.get("evidence", ""))
    if section not in SECTIONS or not rule_name or not evidence:
        return None
    why = _text(upstream.get("why_candidate", "")) or "Upstream structured context requires review."
    extras = {
        key: value
        for key, value in upstream.items()
        if key not in {
            "section", "rule_name", "rule_id", "trigger_source", "why_candidate",
            "evidence", "needs_llm_confirmation"
        }
    }
    imported = _candidate(
        section,
        rule_name,
        rule_ids,
        why,
        evidence,
        trigger_source=_text(upstream.get("trigger_source", "")) or default_trigger,
        extras=extras,
    )
    for key in ("rag_supported", "evidence_strength", "must_review_if_rejected"):
        if key in upstream:
            imported[key] = upstream[key]
    return imported



def _contains_any(text: str, terms: Sequence[str]) -> bool:
    hay = _text(text).casefold()
    return any(term.casefold() in hay for term in terms if _text(term))


_CHRONIC_SCREENING_RE = re.compile(
    r"\b(yearly|annual|annually|every\s+\d+\s+(?:month|year)s?|"
    r"baseline|before pregnancy|planning pregnancy|screen(?:ing)? for complications?|"
    r"screening diabetic|screening and follow[ -]?up|diabetic nephropathy|"
    r"renal damage|retinopathy|lipid profile|ophthalmoscopy|fundus)\b",
    re.IGNORECASE,
)
_ACUTE_TRIGGER_RE = re.compile(
    r"\b(first visit|urgent|emergency|severe|warning signs?|critical|acute|"
    r"confirm diagnosis|suspected|red flag)\b",
    re.IGNORECASE,
)
_PERIODIC_SCREENING_RE = re.compile(
    r"\b(yearly|annual|annually|every\s+\d+\s+(?:month|year)s?)\b",
    re.IGNORECASE,
)
_CHRONIC_CONDITION_RE = re.compile(
    r"\b("
    r"diabetes|hypertension|ckd|chronic\s+kidney|copd|asthma|tb|tuberculosis|"
    r"heart\s+failure|ischemic\s+heart|ischaemic\s+heart|chronic"
    r")\b",
    re.IGNORECASE,
)
_TEST_EQUIVALENTS = (
    ("hba1c", ("hba1c", "hb a1c", "glycated haemoglobin", "glycated hemoglobin")),
    ("rbs", ("random blood sugar", "rbs", "random plasma glucose")),
    ("fpg", ("fasting plasma glucose", "fpg", "fasting blood sugar", "fbs")),
    ("ogtt", ("oral glucose tolerance test", "ogtt")),
    ("cbc", ("cbc", "full blood count", "complete blood count", "platelet", "hematocrit", "haematocrit")),
    ("lft", ("liver function test", "lft", "alt", "ast", "sgpt", "bilirubin")),
)


def _test_present(test: str, documented: str) -> bool:
    hay = _text(documented).casefold()
    if _contains_any(hay, [test]):
        return True
    needle = _text(test).casefold()
    for _, aliases in _TEST_EQUIVALENTS:
        if any(alias in needle for alias in aliases):
            return any(alias in hay for alias in aliases)
    return False


def _acute_audit_investigation_fact(
    fact: Mapping[str, Any],
    prescription: Mapping[str, Any],
) -> bool:
    text = " ".join(
        [
            _text(fact.get("fact_text", "")),
            " ".join(_terms_from_list_field(fact, "recommended_tests")),
            _text(fact.get("rule_focus", "")),
        ]
    )
    if not _CHRONIC_SCREENING_RE.search(text):
        return True
    if _ACUTE_TRIGGER_RE.search(text):
        return True
    context = " ".join(
        _field(prescription, key)
        for key in (
            "Chief_Complaint",
            "Chief Complaint",
            "Diagnosis",
            "Investigation",
            "Investigation_Results",
            "Advice",
        )
    )
    has_chronic_visit_context = bool(
        re.search(
            r"\b(chronic care|annual review|follow[ -]?up|screening visit|complication screening)\b",
            context,
            re.IGNORECASE,
        )
    )
    if _PERIODIC_SCREENING_RE.search(text) and not has_chronic_visit_context:
        return False
    has_results_or_chronic_visit_context = bool(
        _field(prescription, "Investigation_Results", "Investigation Results")
        or has_chronic_visit_context
    )
    if has_results_or_chronic_visit_context and _CHRONIC_CONDITION_RE.search(context) and _condition_matches_prescription(
        fact,
        _field(prescription, "Diagnosis"),
        context,
    ):
        return True
    return bool(
        re.search(
            r"\b(chronic care|annual review|follow[ -]?up|screening visit|complication screening)\b",
            context,
            re.IGNORECASE,
        )
    )


def _rag_facts(section: str, guideline_facts_by_section: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    data = (guideline_facts_by_section or {}).get(section, {})
    if isinstance(data, Mapping):
        return [fact for fact in data.get("facts", []) or [] if isinstance(fact, Mapping)]
    return []


def _fact_evidence(fact: Mapping[str, Any]) -> str:
    bits = []
    for key in ("source_id", "source_page", "condition", "medicine_name", "fact_type"):
        if _text(fact.get(key, "")):
            bits.append(f"{key}={_text(fact.get(key, ''))}")
    text = _text(fact.get("fact_text", ""))
    return f"{' | '.join(bits)} | {text}" if bits else text


def _guideline_candidate_extras(
    fact: Mapping[str, Any],
    *,
    importance: str,
) -> dict[str, Any]:
    """Attach exact RAG provenance to the candidate itself.

    The model should judge a candidate against the specific fact that created
    it, not against a broad section-level pile of guideline context.
    """
    return {
        "guideline_fact_id": fact.get("fact_id"),
        "guideline_fact_evidence": _fact_evidence(fact),
        "guideline_source_id": fact.get("source_id"),
        "guideline_source_page": fact.get("source_page"),
        "guideline_condition": fact.get("condition"),
        "guideline_fact_type": fact.get("fact_type"),
        "importance": importance,
    }


def _terms_from_list_field(fact: Mapping[str, Any], key: str) -> list[str]:
    value = fact.get(key, [])
    if isinstance(value, list):
        return [_text(x) for x in value if _text(x)]
    return [_text(value)] if _text(value) else []



def _norm_for_match(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u0980-\u09ff\s]+", " ", _text(value).casefold()).strip()


def _condition_matches_prescription(fact: Mapping[str, Any], diagnosis: str, clinical: str) -> bool:
    non_condition_aliases = {
        "adult", "adults", "all", "all patients", "child", "children",
        "elderly", "female", "infant", "lactation", "lactating", "male",
        "neonate", "pediatric", "paediatric", "pregnancy", "pregnant", "women",
    }
    condition = _norm_for_match(fact.get("condition", ""))
    aliases = [
        alias
        for alias in (_norm_for_match(x) for x in _terms_from_list_field(fact, "condition_aliases"))
        if alias not in non_condition_aliases
    ]
    context = _norm_for_match(f"{diagnosis} {clinical}")
    diag = _norm_for_match(diagnosis)
    terms = [condition, *aliases]
    for term in terms:
        if not term or term in {"all", "unknown", "medicine", "dosing guidance", "reserve antibiotic"}:
            continue
        if term in context:
            return True
        if diag and (diag in term or term in diag):
            return True
    return False


_CATEGORY_WORDS = {
    "i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5",
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
}


def _category_markers(text: str) -> set[str]:
    norm = _norm_for_match(text)
    markers: set[str] = set()
    for match in re.finditer(r"\b(?:category|cat|grade|stage|group|class)\s*(i{1,3}|iv|v|[1-5]|one|two|three|four|five)\b", norm):
        value = match.group(1).casefold()
        markers.add(_CATEGORY_WORDS.get(value, value))
    return markers


def _fact_category_context_applies(fact: Mapping[str, Any], prescription: Mapping[str, Any]) -> bool:
    """Keep category-specific RAG facts attached to matching category context.

    This is generic retrieval hygiene for guideline rows that use categories,
    stages, grades, groups, or classes. If both the fact and prescription carry
    explicit category markers, they must overlap. If either side lacks a marker,
    we do not block the fact because many guideline rows are not category-bound.
    """
    fact_text = " ".join(
        [
            _text(fact.get("condition", "")),
            _text(fact.get("fact_text", "")),
            _text(fact.get("rule_focus", "")),
            " ".join(_terms_from_list_field(fact, "condition_aliases")),
            " ".join(_terms_from_list_field(fact, "trigger_evidence")),
            " ".join(_terms_from_list_field(fact, "recommended_advice")),
            " ".join(_terms_from_list_field(fact, "referral_criteria")),
            " ".join(_terms_from_list_field(fact, "admission_criteria")),
        ]
    )
    prescription_text = " ".join(_text(value) for value in prescription.values())
    fact_markers = _category_markers(fact_text)
    prescription_markers = _category_markers(prescription_text)
    if not fact_markers or not prescription_markers:
        return True
    return bool(fact_markers & prescription_markers)


def _guideline_fact_applies_to_prescription(
    fact: Mapping[str, Any],
    prescription: Mapping[str, Any],
    patient_context: Mapping[str, Any],
) -> bool:
    return (
        _fact_patient_group_applies(fact, patient_context)
        and _fact_severity_context_applies(fact, prescription)
        and _fact_category_context_applies(fact, prescription)
    )


def _fact_patient_group_applies(fact: Mapping[str, Any], patient_context: Mapping[str, Any]) -> bool:
    """Conservative patient-group filter for guideline facts.

    It suppresses obvious mismatch (child-only facts for adults, adult-only
    facts for children). Unknown/all groups are allowed because many extracted
    guideline rows do not carry complete patient-group metadata yet.
    """
    group_text = _norm_for_match(
        " ".join(
            _text(fact.get(key, ""))
            for key in ("patient_group", "population", "age_group")
        )
    )
    is_child = bool(patient_context.get("is_child"))
    is_elderly = bool(patient_context.get("is_elderly"))
    is_pregnant = bool(patient_context.get("is_pregnant"))
    is_lactating = bool(patient_context.get("is_lactating"))
    fact_text = _norm_for_match(
        " ".join(
            _text(fact.get(key, ""))
            for key in (
                "fact_text",
                "rule_focus",
                "recommended_advice",
                "referral_criteria",
                "admission_criteria",
                "trigger_evidence",
            )
        )
    )
    if any(term in fact_text for term in ("pregnant patient", "pregnant patients", "pregnant women", "pregnancy only")) and not is_pregnant:
        return False
    if any(term in fact_text for term in ("lactating patient", "lactating patients", "breastfeeding women", "breastfeeding patient")) and not is_lactating:
        return False
    if any(term in fact_text for term in ("in children", "children with", "pediatric patients", "paediatric patients", "small children")) and not is_child:
        return False
    if any(term in fact_text for term in ("elderly patients", "older adults", "geriatric patients")) and not is_elderly:
        return False
    if not group_text or group_text in {"all", "all patients", "unknown"}:
        return True
    child_only = any(term in group_text for term in ("child", "children", "paediatric", "pediatric", "infant", "neonate", "adolescent"))
    adult_only = any(term in group_text for term in ("adult", "adults"))
    elderly_only = any(term in group_text for term in ("elderly", "geriatric", "older adult"))
    pregnancy_only = any(term in group_text for term in ("pregnancy", "pregnant", "antenatal"))
    lactation_only = any(term in group_text for term in ("lactation", "lactating", "breastfeeding"))
    if child_only and not is_child:
        return False
    if adult_only and is_child and not child_only:
        return False
    if elderly_only and not is_elderly:
        return False
    if pregnancy_only and not is_pregnant:
        return False
    if lactation_only and not is_lactating:
        return False
    return True


def _fact_severity_context_applies(fact: Mapping[str, Any], prescription: Mapping[str, Any]) -> bool:
    """Do not use severe/complicated-only guideline facts for ordinary cases.

    This is a generic guard: if a fact itself is explicitly about severe,
    complicated, emergency, IV or IM management, the prescription must carry
    matching severe/complicated evidence before that fact can generate a
    candidate. It prevents wrong RAG hits like severe-malaria injection facts
    being used for a non-severe oral regimen review.
    """
    fact_text = _norm_for_match(
        " ".join(
            _text(fact.get(key, ""))
            for key in ("fact_text", "rule_focus", "admission_criteria", "referral_criteria")
        )
    )
    severe_fact = any(
        term in fact_text
        for term in (
            "severe",
            "complicated",
            "emergency treatment",
            "urgent referral",
            "cerebral",
            "iv ",
            "intravenous",
            "im ",
            "intramuscular",
        )
    )
    if not severe_fact:
        return True
    context = _norm_for_match(" ".join(_text(value) for value in prescription.values()))
    return any(
        term in context
        for term in (
            "severe",
            "complicated",
            "emergency",
            "urgent",
            "cerebral",
            "shock",
            "unconscious",
            "convulsion",
            "unable to swallow",
            "vomiting severely",
            "danger sign",
            "warning sign",
        )
    )


def _guideline_candidate_count(grouped: Mapping[str, Sequence[Mapping[str, Any]]], section: str, rule_name: str | None = None) -> int:
    count = 0
    for item in grouped.get(section, []) or []:
        if item.get("trigger_source") != "guideline_rag":
            continue
        if rule_name and item.get("rule_name") != rule_name:
            continue
        count += 1
    return count


def _append_guideline_candidate(grouped: dict[str, list[dict[str, Any]]], candidate: dict[str, Any], *, max_per_rule: int = 4) -> None:
    section = _text(candidate.get("section", ""))
    rule_name = _text(candidate.get("rule_name", ""))
    if section not in grouped:
        return
    if _guideline_candidate_count(grouped, section, rule_name) >= max_per_rule:
        return
    grouped[section].append(candidate)

def _add_guideline_rag_candidates(
    grouped: dict[str, list[dict[str, Any]]],
    rule_ids: Mapping[str, str],
    prescription: Mapping[str, Any],
    patient_context: Mapping[str, Any],
    normalized_medicines: Sequence[Mapping[str, Any]],
    guideline_facts_by_section: Mapping[str, Any] | None,
) -> None:
    diagnosis = _field(prescription, "Diagnosis")
    investigation = _field(prescription, "Investigation")
    investigation_results = _field(prescription, "Investigation_Results", "Investigation Results")
    advice = _field(prescription, "Advice")
    medication = _field(prescription, "Medication")
    clinical = " ".join(_field(prescription, k) for k in ("Chief_Complaint", "Health_Screening", "On_Examination", "Systemic_Examination", "Investigation_Results"))

    # Dx: textbook/national RAG can request diagnosis review when clinical evidence hits
    # a differential/red-flag fact but diagnosis does not mention that condition.
    for fact in _rag_facts("Dx", guideline_facts_by_section)[:8]:
        if not _guideline_fact_applies_to_prescription(fact, prescription, patient_context):
            continue
        ftype = _text(fact.get("fact_type", ""))
        condition = _text(fact.get("condition", ""))
        if ftype in {"diagnostic_clue", "differential_diagnosis", "red_flag", "case_definition", "diagnostic_criteria"} and condition and diagnosis and not _contains_any(diagnosis, [condition]):
            if fact.get("source_id") == "symptom_to_diagnosis_4e" or fact.get("candidate_strength") in {"strong", "conditional"}:
                _append_guideline_candidate(grouped, _candidate(
                    "Dx", "guideline_diagnosis_review_candidate", rule_ids,
                    "Guideline/textbook evidence matched the patient's clinical data; diagnosis should be reviewed against this possibility.",
                    _fact_evidence(fact),
                    trigger_source="guideline_rag",
                    extras=_guideline_candidate_extras(fact, importance="moderate_review"),
                ), max_per_rule=3)

    # Inv: if RAG recommends a concrete test and it is absent from Investigation.
    for fact in _rag_facts("Inv", guideline_facts_by_section)[:8]:
        if not _guideline_fact_applies_to_prescription(fact, prescription, patient_context):
            continue
        if _text(fact.get("fact_type", "")) not in {"recommended_investigation", "diagnostic_test", "screening_criteria", "culture_or_sensitivity", "monitoring"}:
            continue
        if not _acute_audit_investigation_fact(fact, prescription):
            continue
        tests = _terms_from_list_field(fact, "recommended_tests")
        if not tests and _text(fact.get("fact_text", "")):
            tests = [_text(fact.get("fact_text", ""))]
        documented_tests = f"{investigation} {investigation_results}"
        missing = [t for t in tests[:3] if t and not _test_present(t, documented_tests)]
        if missing:
            if _condition_matches_prescription(fact, diagnosis, clinical):
                _append_guideline_candidate(grouped, _candidate(
                    "Inv", "guideline_investigation_missing_candidate", rule_ids,
                    "Matched guideline evidence recommends investigation/monitoring that is not clearly documented.",
                    f"Missing/unclear: {', '.join(missing)} | Guideline: {_fact_evidence(fact)}",
                    trigger_source="guideline_rag",
                    extras=_guideline_candidate_extras(fact, importance="moderate_review"),
                ), max_per_rule=3)

    med_names = [_text(m.get("generic_name", "")) or _text(m.get("brand_name", "")) for m in normalized_medicines]
    for fact in _rag_facts("Rx", guideline_facts_by_section)[:10]:
        if not _guideline_fact_applies_to_prescription(fact, prescription, patient_context):
            continue
        ftype = _text(fact.get("fact_type", ""))
        evidence = _fact_evidence(fact)
        if ftype in {"avoid_medicine", "contraindication", "caution", "precaution", "interaction", "drug_interaction", "renal_hepatic_safety", "pregnancy_safety", "pregnancy_lactation", "adverse_effect", "adverse_drug_reaction"}:
            avoid_terms = _terms_from_list_field(fact, "avoid_medicines") or [_text(fact.get("medicine_name", ""))]
            safety_terms = [
                *_terms_from_list_field(fact, "contraindications_or_cautions"),
                *_terms_from_list_field(fact, "drug_interactions"),
                *_terms_from_list_field(fact, "adverse_effects_or_toxicity"),
                _text(fact.get("fact_text", "")),
            ]
            med_match_terms = [term for term in avoid_terms if term]
            if not med_match_terms and ftype not in {"contraindication", "caution", "precaution", "renal_hepatic_safety", "pregnancy_safety", "pregnancy_lactation", "adverse_effect", "adverse_drug_reaction"}:
                med_match_terms = safety_terms
            if (not med_match_terms or _contains_any(" ".join([medication, *med_names]), med_match_terms)) and _condition_matches_prescription(fact, diagnosis, clinical):
                _append_guideline_candidate(grouped, _candidate(
                    "Rx", "guideline_avoid_medicine_candidate", rule_ids,
                    "Current medicine or patient context matches a guideline medicine-safety fact for the matched clinical context.",
                    evidence, trigger_source="guideline_rag",
                    extras=_guideline_candidate_extras(fact, importance="critical_review"),
                ), max_per_rule=3)
        elif ftype in {"recommended_medicine", "alternative_medicine", "dose_or_duration", "treatment_regimen", "treatment_algorithm", "switch_criteria", "prophylaxis", "coinfection_management", "treatment_failure", "weight_band_dose", "feeding_protocol", "fluid_protocol", "emergency_protocol", "micronutrient_protocol", "nutrition_recipe", "failure_to_respond", "no_antibiotic_care", "antibiotic_indication"}:
            if not _condition_matches_prescription(fact, diagnosis, clinical):
                continue
            medicine_terms = [
                term for term in [
                    _text(fact.get("medicine_name", "")),
                    *_terms_from_list_field(fact, "recommended_medicines"),
                    *_terms_from_list_field(fact, "alternative_medicines"),
                    *_terms_from_list_field(fact, "avoid_medicines"),
                ]
                if _text(term)
            ]
            if medicine_terms and not _contains_any(" ".join([medication, *med_names]), medicine_terms) and ftype != "no_antibiotic_care":
                continue
            duration_mismatch_added = False
            expected_duration = _explicit_guideline_duration_range(fact) if ftype == "dose_or_duration" else None
            if expected_duration and medicine_terms:
                for medicine_item in normalized_medicines:
                    identity = " ".join(
                        _text(medicine_item.get(key, ""))
                        for key in ("raw_text", "generic_name", "brand_name")
                    )
                    if not _contains_any(identity, medicine_terms):
                        continue
                    prescribed_days = _duration_days(_text(medicine_item.get("raw_text", "")))
                    if prescribed_days is None:
                        continue
                    minimum_days, maximum_days = expected_duration
                    if prescribed_days < minimum_days or prescribed_days > maximum_days:
                        _append_guideline_candidate(grouped, _candidate(
                            "Rx", "medicine_duration_mismatch_candidate", rule_ids,
                            "The documented medicine-course duration differs from one explicit, applicable guideline duration.",
                            f"Prescribed: {_text(medicine_item.get('raw_text', ''))} | Guideline: {evidence}",
                            trigger_source="guideline_rag",
                            extras={
                                **_guideline_candidate_extras(fact, importance="moderate_review"),
                                "duration_days": prescribed_days,
                                "guideline_duration_min_days": minimum_days,
                                "guideline_duration_max_days": maximum_days,
                                "generic_id": _text(medicine_item.get("generic_id", "")),
                                "generic_name": _text(medicine_item.get("generic_name", "")),
                            },
                        ), max_per_rule=3)
                        duration_mismatch_added = True
            if duration_mismatch_added:
                continue
            _append_guideline_candidate(grouped, _candidate(
                "Rx", "guideline_treatment_review_candidate", rule_ids,
                "Disease/medicine-specific RAG fact matched; treatment choice, dose, frequency, or duration should be reviewed against it.",
                evidence, trigger_source="guideline_rag",
                extras=_guideline_candidate_extras(fact, importance="moderate_review"),
            ), max_per_rule=3)

    for fact in _rag_facts("Adv", guideline_facts_by_section)[:8]:
        if not _guideline_fact_applies_to_prescription(fact, prescription, patient_context):
            continue
        ftype = _text(fact.get("fact_type", ""))
        if ftype not in {"recommended_advice", "lifestyle_advice", "diet_advice", "exercise_advice", "followup", "referral_criteria", "admission_criteria", "discharge_criteria", "caregiver_counselling", "infection_control", "public_health", "adherence_counselling", "prevention_counselling"}:
            continue
        advice_terms = _terms_from_list_field(fact, "recommended_advice") or _terms_from_list_field(fact, "referral_criteria") or _terms_from_list_field(fact, "followup_monitoring")
        text = _text(fact.get("fact_text", ""))
        if not advice_terms and text:
            advice_terms = [text]
        if (not advice or (advice_terms and not any(_contains_any(advice, [term]) for term in advice_terms[:3]))) and _condition_matches_prescription(fact, diagnosis, clinical):
            _append_guideline_candidate(grouped, _candidate(
                "Adv", "guideline_advice_missing_candidate", rule_ids,
                "Matched guideline evidence recommends advice, follow-up, referral, or counselling not clearly documented.",
                _fact_evidence(fact), trigger_source="guideline_rag",
                extras=_guideline_candidate_extras(fact, importance="moderate_review"),
            ), max_per_rule=3)

def generate_rule_candidates(
    prescription: Mapping[str, Any],
    clinical_evidence: Sequence[Mapping[str, Any]],
    patient_context: Mapping[str, Any],
    normalized_medicines: Sequence[Mapping[str, Any]],
    medicine_facts: Sequence[Mapping[str, Any]] | None = None,
    safety_candidates: Mapping[str, Any] | None = None,
    followup_context: Mapping[str, Any] | None = None,
    guideline_facts_by_section: Mapping[str, Any] | None = None,
    protocol: Any = None,
    live_section_audit: bool = False,
) -> dict[str, Any]:
    """Generate and group deterministic and upstream provisional candidates."""
    rule_ids = _rule_ids(protocol)
    grouped: dict[str, list[dict[str, Any]]] = {section: [] for section in SECTIONS}
    warnings: list[str] = []
    rag_gaps: list[dict[str, Any]] = []
    medicine_facts_by_id = _medicine_fact_index(medicine_facts)
    age = patient_context.get("age_years")
    age_raw = _field(prescription, "Age")
    if age is None or float(age) <= 0:
        grouped["Demo"].append(_candidate(
            "Demo", "age_invalid_candidate", rule_ids,
            "Age is missing or invalid and needs demographic review.",
            age_raw or "Age field is empty",
        ))
    gender = _text(patient_context.get("gender", "")) or _field(prescription, "Gender")
    if not gender:
        grouped["Demo"].append(_candidate(
            "Demo", "gender_missing_candidate", rule_ids,
            "Gender is missing.", "Gender field is empty",
        ))
    if patient_context.get("pregnancy_conflict") or (
        gender.casefold() in {"m", "male"} and patient_context.get("is_pregnant")
    ):
        pregnancy_evidence = patient_context.get("context_evidence", {}).get("is_pregnant", [])
        evidence = "; ".join(
            _text(item.get("evidence", "")) for item in pregnancy_evidence
            if isinstance(item, Mapping)
        ) or "Gender is male while pregnancy context is present"
        grouped["Demo"].append(_candidate(
            "Demo", "male_pregnancy_context_candidate", rule_ids,
            "Male demographic data conflicts with preserved pregnancy evidence.", evidence,
        ))

    history_items = [
        item for item in clinical_evidence
        if _text(item.get("source", "")) == "Past_History"
        and _text(item.get("raw_text", item.get("evidence", "")))
    ]
    for position, item in enumerate(history_items, start=1):
        raw = _text(item.get("raw_text", item.get("evidence", "")))
        normalized = _text(item.get("normalized_text", ""))
        history_evidence = f"Past history: {raw}"
        if normalized and normalized.casefold() != raw.casefold():
            history_evidence += f" | Normalized language hint: {normalized}"
        for rule_name, why, suffix in (
            (
                "history_text_unclear_candidate",
                "Review whether the history text contains a clinically important typo, ambiguous term, or unrecognizable expression; accept understandable Bengali, English, transliteration, and shorthand.",
                "term",
            ),
            (
                "history_demographic_context_candidate",
                "Review the history against age, sex/gender, relevant anatomy, and reproductive context; distinguish true incompatibility from an unusual but possible history.",
                "demographic",
            ),
            (
                "history_detail_candidate",
                "Review whether status, onset, stage, severity, site, procedure, allergen, or reaction detail is missing only where it could materially change present care.",
                "detail",
            ),
            (
                "history_temporal_plausibility_candidate",
                "Review whether the stated historical timing is clinically plausible and sufficiently clear; uncertain claims require verification rather than being declared impossible.",
                "temporal",
            ),
        ):
            grouped["Hx"].append(_candidate(
                "Hx",
                rule_name,
                rule_ids,
                why,
                history_evidence,
                trigger_source="semantic_context_review",
                extras={"candidate_id": f"Hx.{suffix}:{position}"},
            ))

    if history_items:
        history_text = "; ".join(
            f"{_text(item.get('status', 'unspecified')).casefold()}: "
            f"{_text(item.get('raw_text', item.get('evidence', '')))}"
            for item in history_items[:12]
        )
        current_parts = []
        for label, names in (
            ("Chief complaint", ("Chief_Complaint", "Chief Complaint")),
            ("Health screening", ("Health_Screening", "Health Screening")),
            ("On examination", ("On_Examination", "On Examination")),
            ("Systemic examination", ("Systemic_Examination", "Systemic Examination")),
            ("Results", ("Investigation_Results", "Investigation Results")),
            ("Obstetric context", ("ANC_Notes", "ANC/PNC Notes")),
        ):
            value = _field(prescription, *names)
            if value:
                current_parts.append(f"{label}: {value}")
        grouped["Hx"].append(_candidate(
            "Hx",
            "history_context_consistency_candidate",
            rule_ids,
            "Review whether past history directly conflicts with current clinical evidence after accounting for active, resolved, intermittent, and treated states.",
            f"History: {history_text}" + (f" | {' | '.join(current_parts)}" if current_parts else ""),
            trigger_source="semantic_context_review",
            extras={"candidate_id": "Hx.context:1"},
        ))
    if len(history_items) > 1:
        grouped["Hx"].append(_candidate(
            "Hx",
            "history_duplicate_or_conflict_candidate",
            rule_ids,
            "Review history entries for duplicate concepts, incompatible statuses, or direct contradictions; consolidate synonyms without inventing diagnoses.",
            "; ".join(_text(item.get("raw_text", "")) for item in history_items[:12]),
            trigger_source="semantic_context_review",
            extras={"candidate_id": "Hx.duplicate:1"},
        ))

    all_complaint_items = [
        item for item in clinical_evidence
        if _text(item.get("source", "")) == "Chief_Complaint"
        and _text(item.get("raw_text", item.get("evidence", "")))
    ]
    complaint_items = [
        item for item in all_complaint_items
        if _text(item.get("status", "")).casefold() == "present"
    ]
    chief_complaint_text = _field(prescription, "Chief_Complaint", "Chief Complaint")
    if live_section_audit and chief_complaint_text:
        complaint_text_rows = [
            _text(item).strip(" .;")
            for item in re.split(r"[\r\n]+", chief_complaint_text)
            if _text(item).strip(" .;")
        ]
        for position, raw in enumerate(complaint_text_rows, start=1):
            grouped["CC"].append(_candidate(
                "CC", "complaint_not_clinical_candidate", rule_ids,
                "First determine the entry's literal meaning, then review whether it explicitly describes an abnormal symptom, clinical concern, or valid encounter reason. An ordinary action without an abnormal qualifier is not a chief complaint.",
                f"Entered chief complaint: {raw}",
                trigger_source="semantic_context_review",
                extras={"candidate_id": f"CC.validity:{position}"},
            ))
            grouped["CC"].append(_candidate(
                "CC", "complaint_text_unclear_candidate", rule_ids,
                "Review the entered complaint for a clear typographical/transcription error or an unrecognizable clinical expression; accept understandable Bengali or English wording and common shorthand.",
                f"Entered chief complaint: {raw}",
                trigger_source="documentation_review",
                extras={"candidate_id": f"CC.term.unclear:{position}"},
            ))
    meaningful_items = [
        item for item in complaint_items
        if not _is_administrative_complaint(item.get("raw_text", ""))
    ]
    direct_cc_items = [
        item for item in meaningful_items
        if not _is_contact_context_only_complaint(item.get("raw_text", ""))
        and not _is_followup_context_only_complaint(item.get("raw_text", ""))
        and not _is_pregnancy_context_only_complaint(item.get("raw_text", ""))
    ]
    semantic_cc_items = [
        item for item in meaningful_items
        if not _is_contact_context_only_complaint(item.get("raw_text", ""))
        and not _is_followup_context_only_complaint(item.get("raw_text", ""))
    ]
    for position, item in enumerate(semantic_cc_items, start=1):
        raw = _text(item.get("raw_text", ""))
        pregnancy_context_only = _is_pregnancy_context_only_complaint(raw)
        normalized = _text(item.get("normalized_text", ""))
        complaint_evidence = f"Complaint: {raw}"
        if normalized and normalized.casefold() != raw.casefold():
            complaint_evidence += f" | Normalized language hint: {normalized}"
        if not pregnancy_context_only and not _complaint_has_duration(item):
            grouped["CC"].append(_candidate(
                "CC", "duration_missing_candidate", rule_ids,
                "Determine whether this complaint lacks a clinically usable duration or onset; vague words such as recent do not provide a specific timeframe.",
                complaint_evidence,
                trigger_source="semantic_context_review",
                extras={"candidate_id": f"CC.duration:{position}"},
            ))
        if not pregnancy_context_only:
            grouped["CC"].append(_candidate(
                "CC", "severity_missing_candidate", rule_ids,
                "Review whether this complaint needs intensity, grade, measured value, pattern, character, or functional impact for clinically adequate documentation.",
                complaint_evidence,
                trigger_source="semantic_context_review",
                extras={"candidate_id": f"CC.characterization:{position}"},
            ))
            grouped["CC"].append(_candidate(
                "CC", "site_or_source_missing_candidate", rule_ids,
                "Review whether this complaint needs a more specific anatomical site, side, laterality, distribution, or source; do not require these when they are not clinically meaningful.",
                complaint_evidence,
                trigger_source="semantic_context_review",
                extras={"candidate_id": f"CC.anatomy:{position}"},
            ))
        grouped["CC"].append(_candidate(
            "CC", "complaint_demographic_context_candidate", rule_ids,
            "Review the complaint against recorded age, sex/gender, relevant anatomy, and reproductive context; distinguish a true incompatibility from an unusual but possible presentation.",
            complaint_evidence,
            trigger_source="semantic_context_review",
            extras={"candidate_id": f"CC.demographic:{position}"},
        ))
        if _complaint_has_duration(item):
            grouped["CC"].append(_candidate(
                "CC", "complaint_temporal_plausibility_candidate", rule_ids,
                "Review whether the claimed onset or duration is physiologically plausible for the stated phenomenon; uncertain or atypical claims require clarification rather than being declared impossible.",
                complaint_evidence,
                trigger_source="semantic_context_review",
                extras={"candidate_id": f"CC.temporal:{position}"},
            ))

    cross_context_items = [
        item for item in all_complaint_items
        if not _is_administrative_complaint(item.get("raw_text", ""))
        and not _is_contact_context_only_complaint(item.get("raw_text", ""))
        and not _is_followup_context_only_complaint(item.get("raw_text", ""))
    ]
    anc_context = _field(prescription, "ANC_Notes", "ANC/PNC Notes")
    if len(cross_context_items) > 1 or anc_context:
        all_complaints = "; ".join(
            (
                f"{_text(item.get('status', 'unspecified')).casefold()}: "
                f"{_text(item.get('raw_text', item.get('evidence', '')))}"
            )
            for item in cross_context_items[:12]
        )
        cross_evidence = f"Current complaints: {all_complaints}"
        if anc_context:
            cross_evidence += f" | Obstetric context: {anc_context}"
        grouped["CC"].append(_candidate(
            "CC", "complaint_cross_context_consistency_candidate", rule_ids,
            "Review the complaints together with obstetric and clinical context for a direct contradiction; compatible associated states must not be flagged.",
            cross_evidence,
            trigger_source="semantic_context_review",
            extras={"candidate_id": "CC.cross-context:1"},
        ))

    # The LLM, rather than a local threshold rule, decides whether a current
    # complaint conflicts with contemporaneous screening data. Supplying one
    # bounded candidate keeps normal vitals available to CC review without
    # automatically declaring patient-reported symptoms incorrect.
    screening_items = [
        item for item in clinical_evidence
        if _text(item.get("source", "")) == "Health_Screening"
        and _text(item.get("raw_text", item.get("evidence", "")))
    ]
    if live_section_audit and direct_cc_items and screening_items:
        complaint_evidence = "; ".join(
            _text(item.get("raw_text", item.get("evidence", "")))
            for item in direct_cc_items[:6]
        )
        screening_evidence = "; ".join(
            _text(item.get("raw_text", item.get("evidence", "")))
            for item in screening_items[:10]
        )
        grouped["CC"].append(_candidate(
            "CC",
            "complaint_clinical_evidence_inconsistency_candidate",
            rule_ids,
            "Review whether a current patient-reported complaint is inconsistent with contemporaneous screening evidence; consider timing, intermittent symptoms, treatment, and improvement before confirming.",
            f"Chief complaint: {complaint_evidence} | Health screening: {screening_evidence}",
            trigger_source="clinical_context_review",
        ))

    diagnosis = _field(prescription, "Diagnosis")
    active_conditions = [
        item for item in patient_context.get("active_conditions", [])
        if isinstance(item, Mapping)
    ]
    meaningful_active_conditions = [
        item for item in active_conditions
        if _text(item.get("source", "")) != "Past_History"
        and not (
            _text(item.get("source", "")) == "Chief_Complaint"
            and _is_administrative_complaint(item.get("evidence", ""))
        )
    ]
    meaningful_evidence = meaningful_items or meaningful_active_conditions
    if not diagnosis and meaningful_evidence:
        evidence = "; ".join(
            _text(item.get("evidence", item.get("raw_text", "")))
            for item in meaningful_evidence[:3]
        )
        grouped["Dx"].append(_candidate(
            "Dx", "diagnosis_missing_candidate", rule_ids,
            "Clinically meaningful evidence exists but Diagnosis is empty.", evidence,
        ))
    elif diagnosis:
        if live_section_audit:
            grouped["Dx"].append(_candidate(
                "Dx", "diagnosis_text_unclear_candidate", rule_ids,
                "Review the diagnosis wording for a clear typographical/transcription error or unrecognizable diagnostic term; accept established ICD wording, Bengali or English clinical terms, and common abbreviations.",
                f"Entered diagnosis: {diagnosis}",
                trigger_source="documentation_review",
                extras={"candidate_id": "Dx.term.unclear:1"},
            ))
        significant = {
            str(value).casefold()
            for value in _cfg(protocol, "significant_clinical_findings", ("fever", "anemia", "anaemia", "dehydration"))
        }
        for condition in active_conditions:
            # A historical condition may modify today's differential diagnosis
            # or medicine safety without being an active diagnosis that must be
            # repeated in the current assessment.
            if _text(condition.get("source", "")) == "Past_History":
                continue
            concept = _text(condition.get("concept", "")).casefold()
            if concept in significant and not _diagnosis_explains_significant_finding(diagnosis, concept):
                grouped["Dx"].append(_candidate(
                    "Dx", "diagnosis_omits_significant_clinical_finding_candidate", rule_ids,
                    f"Diagnosis does not mention or explain significant active finding {concept}.",
                    _text(condition.get("evidence", "")),
                ))
        for item in _explicit_diagnosis_contradictions(diagnosis, clinical_evidence):
            grouped["Dx"].append(_candidate(
                "Dx", "diagnosis_not_supported_by_clinical_findings_candidate", rule_ids,
                "Diagnosis names a condition explicitly documented as absent/normal in clinical evidence.",
                _text(item.get("raw_text", "")),
            ))

    # PrescribeX writer-time/final review: terminology validity and clinical
    # relevance are deliberately separate questions.  These are provisional
    # candidates, not deterministic findings; the model must decide whether the
    # entered text names a real investigation and, independently, whether each
    # recognized investigation has a clinical link to the available context.
    investigation = _field(prescription, "Investigation")
    investigation_items = _investigation_items(investigation)
    if live_section_audit and investigation_items:
        for position, investigation_item in enumerate(investigation_items, start=1):
            catalog_match = match_investigation(investigation_item)
            catalog_evidence = ""
            if catalog_match:
                catalog_evidence = (
                    f" | Bangladesh catalogue {catalog_match['match_type']}: "
                    f"{catalog_match['canonical_name']} "
                    f"(category: {catalog_match['category']}, "
                    f"source item: {catalog_match['source_no']}, "
                    f"match score: {catalog_match['score']})"
                )
            # An exact selection from a pinned source catalogue has already
            # established identity. Do not ask the LLM to reinterpret the
            # provider's own abbreviations (for example B/V) as possible
            # typos. Fuzzy/manual terms still receive semantic clarification.
            if not catalog_match or catalog_match["match_type"] != "catalog_exact":
                grouped["Inv"].append(_candidate(
                    "Inv", "investigation_term_unrecognized_candidate", rule_ids,
                    "Review whether this entered investigation is confidently recognizable and unambiguous; an unfamiliar abbreviation, likely typo, or unclear local term requires clarification even when an intended test can be guessed.",
                    f"Entered investigation: {investigation_item}{catalog_evidence}",
                    trigger_source="investigation_review",
                    extras={"candidate_id": f"Inv.term.unrecognized:{position}"},
                ))
            evidence = f"Ordered investigation: {investigation_item}"
            if catalog_match:
                evidence += (
                    f" | Standard catalogue identity: {catalog_match['canonical_name']}"
                    f" | Test category: {catalog_match['category']}"
                )
            grouped["Inv"].append(_candidate(
                "Inv", "investigation_relevance_review_candidate", rule_ids,
                "Review this investigation independently; another ordered test being relevant does not establish that this item is relevant.",
                evidence,
                trigger_source="investigation_review",
                extras={"candidate_id": f"Inv.relevance:{position}"},
            ))

    if diagnosis:
        chief_complaint = _field(prescription, "Chief_Complaint", "Chief Complaint")
        if chief_complaint and chief_complaint.casefold() not in {
            "n/a", "na", "none", "nil", "-", "—",
        }:
            grouped["Dx"].append(_candidate(
                "Dx", "diagnosis_not_supported_by_chief_complaint_candidate", rule_ids,
                "Review whether the entered diagnosis plausibly accounts for the chief complaints taken together, including explicit anatomical-site and organ-system consistency.",
                f"Diagnosis: {diagnosis} | Chief complaint: {chief_complaint}",
                trigger_source="live_section_review",
            ))
        demographic_parts = [f"Diagnosis: {diagnosis}"]
        for label, field_names in (
            ("Age", ("Age",)),
            ("Gender", ("Gender", "Sex")),
            ("ANC/PNC", ("ANC_Notes", "ANC/PNC Notes")),
        ):
            value = _field(prescription, *field_names)
            if value:
                demographic_parts.append(f"{label}: {value}")
        grouped["Dx"].append(_candidate(
            "Dx", "diagnosis_demographic_context_candidate", rule_ids,
            "Review whether the diagnosis is genuinely incompatible with recorded age, sex/gender, relevant anatomy, or reproductive context; distinguish biological incompatibility from a rare but possible condition.",
            " | ".join(demographic_parts),
            trigger_source="diagnosis_context_review",
            extras={"candidate_id": "Dx.context.demo:1"},
        ))
        grouped["Dx"].append(_candidate(
            "Dx", "diagnosis_not_supported_by_clinical_findings_candidate", rule_ids,
            "Review whether the entered diagnosis is supported by, or directly contradicts, the available complaints, history, examination findings, and actual investigation results.",
            f"Entered diagnosis: {diagnosis}",
            trigger_source="live_section_review",
        ))

    if live_section_audit:
        for item in clinical_evidence:
            if _text(item.get("source", "")) != "Health_Screening":
                continue
            if _text(item.get("concept", "")).casefold() != "temperature":
                continue
            try:
                value = float(item.get("value"))
            except (TypeError, ValueError):
                continue
            unit = _text(item.get("unit", "")).casefold()
            extreme = (unit == "f" and (value < 90 or value > 107)) or (
                unit == "c" and (value < 32 or value > 42)
            )
            if extreme:
                grouped["Dx"].append(_candidate(
                    "Dx", "extreme_vital_value_candidate", rule_ids,
                    "Temperature is physiologically extreme and may represent a medical emergency or a data-entry/unit error.",
                    _text(item.get("raw_text", "")) or f"Temperature: {value} {unit.upper()}",
                    trigger_source="live_section_review",
                ))

    medication_text = _field(prescription, "Medication")

    # Do not infer an indication error merely because a medicine's monograph
    # indication is not repeated in the diagnosis or complaints. Prescriptions
    # commonly contain supportive, preventive, adjunct, or symptom-relief
    # medicines whose purpose is clinically plausible but not separately
    # documented.  Absence of a direct text match is therefore missing evidence,
    # not positive evidence of inappropriate treatment.  True treatment-choice
    # concerns are generated above from applicable guideline facts (for example,
    # avoid_medicine or no_antibiotic_care), while contraindication and other
    # patient-specific risks are generated by the safety engine.

    for medicine_position, medicine in enumerate(normalized_medicines, start=1):
        raw = _text(medicine.get("raw_text", ""))
        form_and_raw = f"{_text(medicine.get('dosage_form', ''))} {raw}"
        instructions = _instruction_text_for_rule(medicine)
        duration_days = _duration_days(raw)
        frequency_type = _text(medicine.get("frequency_type", ""))
        match_confidence = _text(medicine.get("match_confidence", "")).casefold()
        if live_section_audit and raw and match_confidence in {"unmatched", "inferred_needs_review"}:
            possible = []
            for match in medicine.get("candidate_matches", []) or []:
                if isinstance(match, Mapping):
                    name = _text(match.get("brand_name", "")) or _text(match.get("generic_name", ""))
                else:
                    name = _text(match)
                if name and name not in possible:
                    possible.append(name)
            evidence = f"Entered medicine: {raw} | Mapper status: {match_confidence or 'unmatched'}"
            if possible:
                evidence += " | Possible catalog matches: " + ", ".join(possible[:5])
            grouped["Rx"].append(_candidate(
                "Rx", "medicine_text_unclear_candidate", rule_ids,
                "The medicine mapper could not assign one confident medicine identity; review for a typographical error, ambiguous product name, or genuinely uncatalogued medicine.",
                evidence,
                trigger_source="documentation_review",
                extras={"candidate_id": f"Rx.term.unclear:{medicine_position}"},
            ))
        if (
            raw
            and not _NON_ORAL_FORM.search(form_and_raw)
            and not _timing_rule_exempt(medicine, protocol)
            and _meal_timing_required_by_evidence(medicine, medicine_facts_by_id, protocol)
            and not _has_meal_timing(medicine)
        ):
            grouped["Rx"].append(_candidate(
                "Rx", "medicine_timing_missing_candidate", rule_ids,
                "An oral/unspecified medicine has no detectable before/after-meal timing.", raw,
                extras={
                    "generic_id": _text(medicine.get("generic_id", "")),
                    "generic_name": _text(medicine.get("generic_name", "")),
                },
            ))
        timing_mismatch = _medicine_timing_mismatch(medicine, medicine_facts_by_id)
        if (
            raw
            and not _NON_ORAL_FORM.search(form_and_raw)
            and not _timing_rule_exempt(medicine, protocol)
            and timing_mismatch is not None
        ):
            prescribed_timing, timing_fact = timing_mismatch
            grouped["Rx"].append(_candidate(
                "Rx", "medicine_timing_mismatch_candidate", rule_ids,
                "The documented meal interval is shorter than the minimum interval in retrieved medicine-administration evidence.",
                f"Prescription: {prescribed_timing} | Retrieved administration evidence: {timing_fact}",
                trigger_source="medicine_evidence",
                extras={
                    "generic_id": _text(medicine.get("generic_id", "")),
                    "generic_name": _text(medicine.get("generic_name", "")),
                    "medicine_fact_evidence": timing_fact,
                },
            ))
        if (
            raw
            and medicine.get("strength_mg") is not None
            and duration_days is not None
            and frequency_type == "unknown"
        ):
            grouped["Rx"].append(_candidate(
                "Rx", "frequency_missing_or_ambiguous_candidate", rule_ids,
                "Medicine has a documented strength and course duration, but no safely parseable dose frequency.",
                raw,
                extras={
                    "generic_id": _text(medicine.get("generic_id", "")),
                    "generic_name": _text(medicine.get("generic_name", "")),
                },
            ))
        if (
            raw
            and duration_days is None
            and not _has_ongoing_duration(raw)
            and frequency_type != "stat_single_dose"
        ):
            grouped["Rx"].append(_candidate(
                "Rx", "medicine_duration_missing_or_ambiguous_candidate", rule_ids,
                "Medicine course duration is missing or cannot be safely interpreted.",
                raw,
                extras={
                    "generic_id": _text(medicine.get("generic_id", "")),
                    "generic_name": _text(medicine.get("generic_name", "")),
                },
            ))
        dosage_form_text = _text(medicine.get("dosage_form", ""))
        raw_without_instructions = re.sub(r"\[[^\]]*\]", " ", raw)
        form_route = _dosage_form_route(dosage_form_text or raw_without_instructions)
        instruction_route = _instruction_route(instructions)
        if form_route and instruction_route and form_route != instruction_route:
            grouped["Rx"].append(_candidate(
                "Rx", "route_inconsistent_candidate", rule_ids,
                "The documented administration route conflicts with the medicine dosage form.",
                f"Dosage form: {_text(medicine.get('dosage_form', '')) or 'derived from medicine text'} | Medicine: {raw} | Interpreted instruction route: {instruction_route}",
                extras={
                    "generic_id": _text(medicine.get("generic_id", "")),
                    "generic_name": _text(medicine.get("generic_name", "")),
                    "dosage_form": dosage_form_text,
                    "instruction_route": instruction_route,
                    "instruction_text": instructions,
                },
            ))
        route_instruction_text = translated_text(instructions)
        if _ROUTE_FORM.search(form_and_raw) and not _ROUTE_INSTRUCTION.search(route_instruction_text):
            grouped["Rx"].append(_candidate(
                "Rx", "medicine_route_or_instruction_incomplete_candidate", rule_ids,
                "A route-specific medicine lacks a detectable administration route/instruction.", raw,
                extras={
                    "generic_id": _text(medicine.get("generic_id", "")),
                    "generic_name": _text(medicine.get("generic_name", "")),
                },
            ))
        if _SOLID_FORM.search(form_and_raw) and _DYSPHAGIA.search(
            " ".join(
                _field(prescription, name)
                for name in ("Chief Complaint", "Chief_Complaint", "On Examination", "On_Examination", "Diagnosis")
            )
        ):
            grouped["Rx"].append(_candidate(
                "Rx", "dosage_form_inappropriate_candidate", rule_ids,
                "Solid oral dosage form is prescribed while swallowing difficulty/dysphagia is documented.",
                raw,
                extras={
                    "generic_id": _text(medicine.get("generic_id", "")),
                    "generic_name": _text(medicine.get("generic_name", "")),
                },
            ))
        if (
            patient_context.get("is_child")
            and _text(medicine.get("frequency_type", "")) == "liquid_spoon_daily"
            and medicine.get("calculated_daily_dose_mg") is None
            and medicine.get("amount_per_dose") is not None
            and medicine.get("times_per_day") is not None
        ):
            grouped["Rx"].append(_candidate(
                "Rx", "pediatric_liquid_weight_dose_review_candidate", rule_ids,
                "Child has liquid spoon dosing documented; mg/kg review needs configured spoon volume and patient weight confirmation.",
                raw,
                extras={
                    "generic_id": _text(medicine.get("generic_id", "")),
                    "generic_name": _text(medicine.get("generic_name", "")),
                    "amount_per_dose": medicine.get("amount_per_dose"),
                    "times_per_day": medicine.get("times_per_day"),
                },
            ))

    for upstream, trigger in (
        (safety_candidates or {}, "safety_candidate_engine"),
        (followup_context or {}, "followup_context_engine"),
    ):
        for warning in upstream.get("warnings", []):
            if _text(warning):
                warnings.append(_text(warning))
        for gap in upstream.get("rag_gaps", []):
            if isinstance(gap, Mapping):
                rag_gaps.append(dict(gap))
        for item in upstream.get("candidates", []):
            if not isinstance(item, Mapping):
                continue
            imported = _import_candidate(item, rule_ids, trigger)
            if imported:
                grouped[imported["section"]].append(imported)
            else:
                warnings.append("an upstream candidate was skipped because section/rule/evidence was incomplete")
    for fact_result in medicine_facts or []:
        for warning in fact_result.get("warnings", []):
            if _text(warning):
                warnings.append(_text(warning))
    for warning in patient_context.get("warnings", []):
        if _text(warning):
            warnings.append(_text(warning))

    advice = _field(prescription, "Advice")
    advice_context_parts = []
    for label, names in (
        ("Age", ("Age",)),
        ("Gender", ("Gender", "Sex")),
        ("Chief complaint", ("Chief_Complaint", "Chief Complaint")),
        ("Past history", ("Past_History", "Past History")),
        ("Obstetric context", ("ANC_Notes", "ANC/PNC Notes", "Obstetric Status")),
        ("Health screening", ("Health_Screening", "Health Screening")),
        ("On examination", ("On_Examination", "On Examination")),
        ("Systemic examination", ("Systemic_Examination", "Systemic Examination")),
        ("Investigation results", ("Investigation_Results", "Investigation Results")),
        ("Diagnosis", ("Diagnosis",)),
        ("Medication", ("Medication", "Medicine")),
    ):
        value = _field(prescription, *names)
        if value:
            advice_context_parts.append(f"{label}: {value}")
    advice_context = " | ".join(advice_context_parts)
    if live_section_audit and not advice and (diagnosis or medication_text):
        grouped["Adv"].append(_candidate(
            "Adv", "advice_missing_candidate", rule_ids,
            "Diagnosis or treatment is documented, but no patient advice, counselling, precaution, or safety-net instruction is recorded.",
            "Advice is empty" + (f" | Diagnosis: {diagnosis}" if diagnosis else "") + (f" | Medication: {medication_text}" if medication_text else ""),
            trigger_source="live_section_review",
        ))
    elif live_section_audit and advice:
        grouped["Adv"].append(_candidate(
            "Adv", "advice_content_unrecognized_candidate", rule_ids,
            "Review whether the entered Advice contains a recognizable patient instruction and whether it has a clear typographical/transcription error; accept understandable Bengali or English wording and common shorthand.",
            f"Entered advice: {advice}" + (f" | Diagnosis: {diagnosis}" if diagnosis else "") + (f" | Medication: {medication_text}" if medication_text else ""),
            trigger_source="live_section_review",
        ))
    if advice:
        grouped["Adv"].append(_candidate(
            "Adv", "advice_context_appropriateness_candidate", rule_ids,
            "Review each entered advice item for clinical relevance, compatibility, and safety against the complete available patient and prescription context; flag unrelated, contradicted, or unsafe advice without demanding unnecessary generic counselling.",
            f"Entered advice: {advice}" + (f" | {advice_context}" if advice_context else ""),
            trigger_source="semantic_context_review",
            extras={"candidate_id": "Adv.context:1"},
        ))
        followup_matches = re.findall(
            r"(?:^|,\s*)Follow-up:\s*([^,]+(?:,(?!\s*[A-Z][A-Za-z /_-]*:)[^,]+)*)",
            advice,
            flags=re.IGNORECASE,
        )
        if followup_matches:
            followup_text = "; ".join(_text(value) for value in followup_matches if _text(value))
            grouped["Adv"].append(_candidate(
                "Adv", "advice_followup_interval_candidate", rule_ids,
                "Review whether the documented follow-up timing and return plan are appropriate for current risk, pregnancy/obstetric context, abnormalities, diagnosis, treatment duration, monitoring needs, and safety-net urgency; confirm only clearly inappropriate or contradictory timing.",
                f"Follow-up plan: {followup_text}" + (f" | {advice_context}" if advice_context else ""),
                trigger_source="semantic_context_review",
                extras={"candidate_id": "Adv.followup.interval:1"},
            ))
    danger_evidence = _pregnancy_danger_evidence(prescription)
    if (
        danger_evidence
        and _has_pregnancy_context(prescription, patient_context)
        and not _advice_has_danger_coverage(advice)
    ):
        grouped["Adv"].append(_candidate(
            "Adv", "pregnancy_danger_sign_action_missing_candidate", rule_ids,
            "Pregnancy danger-sign evidence exists but urgent assessment, referral, follow-up, or danger-sign counseling is not documented.",
            danger_evidence,
            extras={"severity": "needs_review"},
        ))

    _add_guideline_rag_candidates(
        grouped,
        rule_ids,
        prescription,
        patient_context,
        normalized_medicines,
        guideline_facts_by_section,
    )

    if live_section_audit:
        _add_protocol_review_candidates(grouped, rule_ids, prescription, protocol)

    for section in SECTIONS:
        unique: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for item in grouped[section]:
            key = item["rule_name"], item["evidence"], _text(item.get("generic_id", ""))
            if key not in seen:
                seen.add(key)
                unique.append(item)
        grouped[section] = unique
    return {
        "candidates_by_section": grouped,
        "warnings": list(dict.fromkeys(warnings)),
        "rag_gaps": list({str(item): item for item in rag_gaps}.values()),
    }

# Protocol concerns that the deterministic engines never turn into candidates (for example every
# Inv.cc / Inv.dx / Inv.hs / Inv.demo.* rule). Without these the LLM was never asked to review them,
# and a section with no candidates was skipped entirely, so such errors could not be detected.
_PROTOCOL_REVIEW_EXCLUDED = {
    "Dx.followup", "Dx.inv.1", "Rx.followup.dup", "Rx.followup.ddi", "Adv.followup",
    "Adv.followup.interval", "Inv.followup.redundant",
}
_PROTOCOL_REVIEW_SECTIONS = ("Dx", "Inv", "Rx", "Adv")
_ALIASED_RULE_IDS = {"CC.demo", "Dx.demo", "Rx.dup"}  # produced under another id (e.g. CC.context.demo)


def _protocol_review_context(section: str, prescription: Mapping[str, Any]) -> str:
    fields = {
        "Dx": ("Diagnosis", "Chief_Complaint", "Health_Screening", "Investigation_Results"),
        "Inv": ("Diagnosis", "Investigation", "Investigation_Results", "Chief_Complaint", "Health_Screening",
                "On_Examination", "Systemic_Examination", "Age", "Gender"),
        "Rx": ("Diagnosis", "Medication", "Chief_Complaint", "Health_Screening", "Age", "Gender"),
        "Adv": ("Diagnosis", "Medication", "Advice", "Chief_Complaint", "Health_Screening"),
    }[section]
    parts = [f"{name}: {_field(prescription, name) or 'none'}" for name in fields]
    return " | ".join(parts)


def _add_protocol_review_candidates(
    grouped: dict[str, list[dict[str, Any]]],
    rule_ids: Mapping[str, str],
    prescription: Mapping[str, Any],
    protocol: Any,
) -> None:
    concerns = _cfg(protocol, "concerns", {})
    if not isinstance(concerns, Mapping):
        return
    generated = set(rule_ids.values()) | _ALIASED_RULE_IDS
    diagnosis = _field(prescription, "Diagnosis")
    anchor = diagnosis or _field(prescription, "Chief_Complaint", "Chief Complaint")
    if not anchor or anchor.casefold() in {"n/a", "na", "none", "nil", "-", "—"}:
        return
    for section in _PROTOCOL_REVIEW_SECTIONS:
        for rule in concerns.get(section, []) or []:
            if not isinstance(rule, Mapping):
                continue
            concern_id = _text(rule.get("id", ""))
            if not concern_id or concern_id in generated or concern_id in _PROTOCOL_REVIEW_EXCLUDED:
                continue
            if any(item.get("rule_id") == concern_id for item in grouped[section]):
                continue
            name = f"{concern_id.replace('.', '_').lower()}_protocol_review_candidate"
            grouped[section].append(_candidate(
                section, name, {name: concern_id},
                f"Rulebook check {concern_id}: decide whether the evidence clearly violates this rule; the rule text is in the task instructions. Omit when the evidence is acceptable.",
                _protocol_review_context(section, prescription),
                trigger_source="protocol_review",
                extras={"candidate_id": f"{concern_id}:1"},
            ))


__all__ = ["SECTIONS", "generate_rule_candidates"]
