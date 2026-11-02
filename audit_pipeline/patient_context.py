"""Build structured patient context without making audit decisions."""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from .clinical_finding_interpreter import interpret_clinical_findings
from .text_translation import translated_text


_PREGNANCY = re.compile(
    r"\b(pregnan(?:t|cy)|gestation(?:al)?|gravid(?:a)?|antenatal|anc\b|trimester)\b",
    re.IGNORECASE,
)
_PREGNANCY_NEGATION = re.compile(
    r"\b(not pregnant|no pregnancy|pregnancy negative|negative pregnancy)\b",
    re.IGNORECASE,
)
_LACTATION = re.compile(
    r"\b(lactat(?:ing|ion)|breast[ -]?feed(?:ing)?|breastfed|nursing mother)\b",
    re.IGNORECASE,
)
_LACTATION_NEGATION = re.compile(
    r"\b(not lactating|no lactation|not breast[ -]?feeding)\b",
    re.IGNORECASE,
)

_RISK_SOURCES = (
    "Past_History",
    "Investigation_Results",
    "Investigation",
    "Diagnosis",
    "Chief_Complaint",
    "Health_Screening",
    "On_Examination",
    "Systemic_Examination",
    "ANC_Notes",
    "Advice",
)
_RENAL_VALUE = re.compile(
    r"\b(?P<label>eGFR|GFR|CrCl|creatinine clearance)\b\s*[:=]?\s*"
    r"(?P<value>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>mL\s*/\s*min(?:\s*/\s*1\.73\s*m(?:2|²))?)?",
    re.IGNORECASE,
)
_RENAL_DISEASE = re.compile(
    r"\b(?:CKD(?:\s+stage\s+(?P<stage>[1-5]))?|chronic kidney disease|"
    r"renal failure|renal impairment|kidney disease)\b",
    re.IGNORECASE,
)
_HEPATIC_DISEASE = re.compile(
    r"\b(?:liver disease|hepatic impairment|hepatic failure|cirrhosis|jaundice)\b",
    re.IGNORECASE,
)
_ABNORMAL_LIVER_LAB = re.compile(
    r"\b(?:(?:abnormal|elevated|high)\s+(?P<label1>SGPT|ALT|AST|bilirubin)"
    r"(?:\s*[:=]?\s*(?P<value1>\d+(?:\.\d+)?))?|"
    r"(?P<label2>SGPT|ALT|AST|bilirubin)(?:\s*[:=]?\s*(?P<value2>\d+(?:\.\d+)?))?"
    r"\s*(?:is\s+)?(?:abnormal|elevated|high))\b",
    re.IGNORECASE,
)
_ALLERGY_TO = re.compile(
    r"\b(?:allerg(?:y|ic)\s+to|hypersensitivity\s+to)\s+"
    r"(?P<substance>[A-Za-z][A-Za-z0-9 +/\-]{0,50}?)(?=\s*[,;.]|$)",
    re.IGNORECASE,
)
_POTASSIUM_RESULT = re.compile(
    r"\b(?:potassium|serum\s+potassium|k\+?)\b\s*[:= -]*\s*"
    r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>mmol/L|mEq/L)?",
    re.IGNORECASE,
)
_RENAL_TEST = re.compile(
    r"\b(renal function|rft|creatinine|eGFR|GFR|CrCl|kidney function)\b",
    re.IGNORECASE,
)
_NAMED_ALLERGY = re.compile(
    r"\b(?P<substance>penicillins?|sulfa|sulfonamides?|drug)\s+"
    r"(?:allerg(?:y|ies)|hypersensitivity)\b",
    re.IGNORECASE,
)
_REACTION_AFTER_DRUG = re.compile(
    r"\b(?:rash|urticaria|hives|angioedema|anaphylaxis|hypersensitivity|allergic\s+reaction|drug\s+reaction)\s+"
    r"(?:after|with|following|to|due\s+to)\s+"
    r"(?P<substance>[A-Za-z][A-Za-z0-9 +/\-]{0,50}?)(?=\s*[,;.]|\s+history\b|\s+noted\b|$)|"
    r"\b(?P<substance2>penicillins?|sulfa|sulfonamides?|amoxicillin|cephalosporins?)\s+"
    r"(?:caused|associated\s+with)\s+"
    r"(?:rash|urticaria|hives|angioedema|anaphylaxis|hypersensitivity|allergic\s+reaction)\b",
    re.IGNORECASE,
)
_NEGATED_FINDING_PREFIX = re.compile(
    r"\b(?:no|without|denies|denied)\s*$", re.IGNORECASE
)
_NEGATED_FINDING_SUFFIX = re.compile(
    r"^\s*(?:[.:-]\s*)?(?:\(\s*-\s*\)|"
    r"\(?\s*(?:absent|negative|normal|nad)\s*\)?)",
    re.IGNORECASE,
)

_FIELD_ALIASES = {
    "Chief Complaint": "Chief_Complaint",
    "Chief Complain": "Chief_Complaint",
    "Past History": "Past_History",
    "Medical History": "Past_History",
    "ANC/PNC Notes": "ANC_Notes",
    "ANC PNC Notes": "ANC_Notes",
    "Investigation Result": "Investigation_Results",
    "Investigation Results": "Investigation_Results",
    "Health Screening": "Health_Screening",
    "On Examination": "On_Examination",
    "Systemic Examination": "Systemic_Examination",
}


def _text(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text.casefold() in {"", "nan", "none", "null"} else text


def _normalized_record(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        _FIELD_ALIASES.get(str(key).strip(), str(key).strip()): value
        for key, value in record.items()
    }


def _parse_age_years(value: Any) -> float | None:
    text = _text(value)
    if not text:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    age = float(match.group(0))
    lowered = text.casefold()
    if re.search(r"\bmonths?\b|\bmos?\b", lowered):
        age /= 12.0
    elif re.search(r"\bweeks?\b|\bwks?\b", lowered):
        age /= 52.1429
    elif re.search(r"\bdays?\b", lowered):
        age /= 365.25
    return age if age >= 0 else None


def _normalize_gender(value: Any) -> str:
    text = _text(value).casefold()
    if text in {"f", "female", "woman", "girl"}:
        return "F"
    if text in {"m", "male", "man", "boy"}:
        return "M"
    return text.upper()


def _flag_evidence(
    record: Mapping[str, Any],
    sources: Sequence[str],
    positive: re.Pattern[str],
    negative: re.Pattern[str],
) -> list[dict[str, str]]:
    evidence = []
    for source in sources:
        text = _text(record.get(source, ""))
        normalized = translated_text(text)
        if text and positive.search(normalized) and not negative.search(normalized):
            evidence.append({"source": source, "evidence": text})
    return evidence


def _clinical_flag_evidence(
    clinical_evidence: Sequence[Mapping[str, Any]],
    source: str,
    positive: re.Pattern[str],
) -> list[dict[str, str]]:
    matches = []
    for item in clinical_evidence:
        if str(item.get("source", "")) != source:
            continue
        if str(item.get("status", "")).casefold() not in {"present", "abnormal"}:
            continue
        raw = _text(item.get("raw_text", ""))
        concept = _text(item.get("concept", ""))
        interpretation = _text(item.get("interpretation", ""))
        normalized = translated_text(" ".join((concept, raw, interpretation)))
        if positive.search(normalized):
            matches.append({"source": source, "evidence": raw or concept})
    return matches


def _active_conditions(
    clinical_evidence: Sequence[Mapping[str, Any]],
) -> list[dict[str, str]]:
    active: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in clinical_evidence:
        status = str(item.get("status", "")).strip().casefold()
        if status not in {"present", "abnormal"}:
            continue
        concept = _text(item.get("concept", ""))
        interpretation = _text(item.get("interpretation", ""))
        if interpretation.casefold() == "fever":
            concept = "fever"
        concept = concept.strip(" .:-").casefold()
        source = _text(item.get("source", ""))
        evidence = _text(item.get("raw_text", ""))
        if not concept or not source:
            continue
        key = concept, source, evidence
        if key in seen:
            continue
        seen.add(key)
        active.append(
            {
                "concept": concept,
                "status": status,
                "source": source,
                "evidence": evidence,
            }
        )
    return active


def _risk_item(
    risk_type: str,
    source: str,
    evidence: str,
    *,
    value: float | int | None = None,
    unit: str | None = None,
    substance: str | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "risk_type": risk_type,
        "source": source,
        "evidence": evidence,
    }
    if value is not None:
        item["value"] = value
    if unit:
        item["unit"] = unit
    if substance:
        item["substance"] = substance
    return item


def _match_is_negated(text: str, match: re.Match[str]) -> bool:
    """Recognize simple local negation without inferring clinical meaning."""
    prefix = text[max(0, match.start() - 24) : match.start()]
    suffix = text[match.end() : min(len(text), match.end() + 32)]
    return bool(
        _NEGATED_FINDING_PREFIX.search(prefix)
        or _NEGATED_FINDING_SUFFIX.search(suffix)
    )


def _field_risks(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    risks: list[dict[str, Any]] = []
    investigation = _text(record.get("Investigation", ""))
    investigation_results = _text(record.get("Investigation_Results", ""))
    if investigation and _RENAL_TEST.search(investigation) and not investigation_results:
        risks.append(
            _risk_item(
                "renal_monitoring_missing",
                "Investigation",
                investigation,
            )
        )
    for source in _RISK_SOURCES:
        text = _text(record.get(source, ""))
        if not text:
            continue
        for match in _RENAL_VALUE.finditer(text):
            value = float(match.group("value"))
            if value >= 60:
                continue
            risks.append(
                _risk_item(
                    "renal_impairment",
                    source,
                    match.group(0).strip(),
                    value=value,
                    unit=re.sub(r"\s+", "", match.group("unit") or "") or None,
                )
            )
        for match in _RENAL_DISEASE.finditer(text):
            if _match_is_negated(text, match):
                continue
            stage = match.groupdict().get("stage")
            risks.append(
                _risk_item(
                    "renal_impairment",
                    source,
                    match.group(0),
                    value=int(stage) if stage else None,
                    unit="CKD stage" if stage else None,
                )
            )
        for match in _HEPATIC_DISEASE.finditer(text):
            if _match_is_negated(text, match):
                continue
            risks.append(
                _risk_item("hepatic_impairment", source, match.group(0))
            )
        for match in _ABNORMAL_LIVER_LAB.finditer(text):
            if _match_is_negated(text, match):
                continue
            label = match.group("label1") or match.group("label2") or "liver test"
            raw_value = match.group("value1") or match.group("value2")
            risks.append(
                _risk_item(
                    "hepatic_impairment",
                    source,
                    match.group(0),
                    value=float(raw_value) if raw_value else None,
                    unit=label.upper(),
                )
            )
        for pattern in (_ALLERGY_TO, _NAMED_ALLERGY, _REACTION_AFTER_DRUG):
            for match in pattern.finditer(text):
                if _match_is_negated(text, match):
                    continue
                substance = (match.groupdict().get("substance") or match.groupdict().get("substance2") or "").strip(" .:-")
                substance = re.sub(r"\s+(?:history|noted)$", "", substance, flags=re.IGNORECASE).strip(" .:-")
                if not substance:
                    continue
                risks.append(
                    _risk_item(
                        "allergy_hypersensitivity",
                        source,
                        match.group(0),
                        substance=substance,
                    )
                )
        for match in _POTASSIUM_RESULT.finditer(text):
            value = float(match.group("value"))
            if value < 5.5:
                continue
            risks.append(
                _risk_item(
                    "hyperkalemia",
                    source,
                    match.group(0).strip(),
                    value=value,
                    unit=match.group("unit") or None,
                )
            )
    return risks


def _context_risks(
    pregnancy_evidence: Sequence[Mapping[str, str]],
    lactation_evidence: Sequence[Mapping[str, str]],
    *,
    is_child: bool,
    is_elderly: bool,
    age_text: str,
) -> list[dict[str, Any]]:
    risks = [
        _risk_item("pregnancy", item["source"], item["evidence"])
        for item in pregnancy_evidence
    ]
    risks.extend(
        _risk_item("lactation", item["source"], item["evidence"])
        for item in lactation_evidence
    )
    if is_child:
        risks.append(_risk_item("pediatric", "Age", age_text))
    if is_elderly:
        risks.append(_risk_item("elderly", "Age", age_text))
    return risks


def _deduplicate_risks(risks: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for risk in risks:
        key = (
            _text(risk.get("risk_type", "")).casefold(),
            _text(risk.get("source", "")),
            _text(risk.get("evidence", "")).casefold(),
            _text(risk.get("substance", "")).casefold(),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(dict(risk))
    return result


AGE_MISSING_WARNING = "age is missing or could not be parsed"
MALE_PREGNANCY_WARNING = (
    "male gender conflicts with pregnancy evidence; evidence was preserved for demographic review "
    "but pregnancy-specific guidance was not activated"
)
# Patient-context warnings that describe a likely documentation error in the prescription itself (not a
# pipeline problem), with the wording shown to the clinician as a "possible error".
CLINICAL_WARNING_MESSAGES = {
    AGE_MISSING_WARNING: "Age is missing or unreadable. Record the patient's age (months or days for an infant).",
    MALE_PREGNANCY_WARNING: "Pregnancy is documented for a male patient. Verify the recorded gender or the pregnancy entry.",
}


def build_patient_context(
    prescription: Mapping[str, Any],
    clinical_evidence: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create normalized demographic and clinical context with provenance."""
    record = _normalized_record(prescription)
    evidence = (
        list(clinical_evidence)
        if clinical_evidence is not None
        else interpret_clinical_findings(record)
    )
    warnings: list[str] = []
    age_years = _parse_age_years(record.get("Age", ""))
    if age_years is None:
        warnings.append(AGE_MISSING_WARNING)
    gender = _normalize_gender(record.get("Gender", ""))

    pregnancy_evidence = _flag_evidence(
        record,
        ("Diagnosis", "Chief_Complaint", "Past_History", "ANC_Notes"),
        _PREGNANCY,
        _PREGNANCY_NEGATION,
    )
    pregnancy_evidence.extend(
        _clinical_flag_evidence(evidence, "Health_Screening", _PREGNANCY)
    )
    lactation_evidence = _flag_evidence(
        record,
        ("Advice", "Diagnosis", "ANC_Notes"),
        _LACTATION,
        _LACTATION_NEGATION,
    )
    pregnancy_conflict = gender == "M" and bool(pregnancy_evidence)
    effective_pregnancy_evidence = [] if pregnancy_conflict else pregnancy_evidence
    if pregnancy_conflict:
        warnings.append(MALE_PREGNANCY_WARNING)

    is_child = age_years is not None and age_years < 18
    is_elderly = age_years is not None and age_years >= 60
    context_evidence: dict[str, list[dict[str, str]]] = {
        "is_child": [],
        "is_elderly": [],
        "is_pregnant": pregnancy_evidence,
        "is_lactating": lactation_evidence,
    }
    age_text = _text(record.get("Age", ""))
    if age_years is not None:
        age_fact = {"source": "Age", "evidence": age_text}
        if is_child:
            context_evidence["is_child"].append(age_fact)
        if is_elderly:
            context_evidence["is_elderly"].append(age_fact)

    patient_risks = _deduplicate_risks(
        [
            *_field_risks(record),
            *_context_risks(
                effective_pregnancy_evidence,
                lactation_evidence,
                is_child=is_child,
                is_elderly=is_elderly,
                age_text=age_text,
            ),
        ]
    )

    return {
        "age_years": age_years,
        "gender": gender,
        "is_child": is_child,
        "is_elderly": is_elderly,
        "is_pregnant": bool(effective_pregnancy_evidence),
        "pregnancy_conflict": pregnancy_conflict,
        "is_lactating": bool(lactation_evidence),
        "active_conditions": _active_conditions(evidence),
        "patient_risks": patient_risks,
        "context_evidence": context_evidence,
        "clinical_context_text": " ".join(
            _text(record.get(source, "")) for source in _RISK_SOURCES
        ),
        "warnings": warnings,
    }


__all__ = ["CLINICAL_WARNING_MESSAGES", "build_patient_context"]
