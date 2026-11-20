"""Convert prescription fields into structured clinical evidence.

This module records evidence only; it does not decide documentation errors.
Investigation orders are intentionally excluded because they are requests, not
patient findings. Investigation results are included because they can contain
clinical evidence such as abnormal labs.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .frequency_parser import normalize_digits
from .text_translation import translated_text

CLINICAL_SOURCES = (
    "Chief_Complaint",
    "Past_History",
    "Health_Screening",
    "On_Examination",
    "Systemic_Examination",
    "Investigation_Results",
)

_EMPTY_VALUES = {"", "nan", "none", "null"}
_STATUS_TERMS = {
    "+": "present",
    "-": "absent",
    "absent": "absent",
    "no dehydration": "absent",
    "nad": "normal",
    "normal": "normal",
    "negative": "absent",
    "present": "present",
    "positive": "present",
    "abnormal": "abnormal",
}
_ALIASES = {
    "Chief Complaint": "Chief_Complaint",
    "Chief Complain": "Chief_Complaint",
    "Past History": "Past_History",
    "Medical History": "Past_History",
    "Health Screening": "Health_Screening",
    "On Examination": "On_Examination",
    "Systemic Examination": "Systemic_Examination",
    "Investigation Results": "Investigation_Results",
    "Investigation Result": "Investigation_Results",
}


def _text(value: Any) -> str:
    result = str(value or "").strip()
    return "" if result.lower() in _EMPTY_VALUES else result


def _normalize_concept(concept: str) -> str:
    words = re.sub(r"\s+", " ", concept.strip()).split(" ")
    return " ".join(
        word if word.isupper() and len(word) <= 5
        else word[:1].upper() + word[1:].lower()
        for word in words if word
    )


def _item(
    concept: str,
    status: str,
    source: str,
    raw_text: str,
    *,
    value: Any | None = None,
    unit: str | None = None,
    interpretation: str | None = None,
    severity: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "concept": _normalize_concept(concept),
        "status": status,
        "source": source,
        "raw_text": raw_text.strip(),
    }
    normalized_text = translated_text(raw_text)
    if normalized_text and normalized_text != raw_text.strip():
        result["normalized_text"] = normalized_text
    if value is not None:
        result["value"] = value
    if unit is not None:
        result["unit"] = unit
    if interpretation is not None:
        result["interpretation"] = interpretation
    if severity is not None:
        result["severity"] = severity
    return result


_DURATION_AT_END = re.compile(
    r"\b(?:for\s+|since\s+)?\d+(?:\.\d+)?\s*(?:hour|day|week|month|year)s?\b\s*$",
    re.IGNORECASE,
)


def _duration_normalized_text(raw: str) -> str:
    text = translated_text(normalize_digits(raw))
    text = re.sub(r"দিন", " day ", text)
    text = re.sub(r"সপ্তাহ", " week ", text)
    text = re.sub(r"মাস", " month ", text)
    text = re.sub(r"বছর", " year ", text)
    text = re.sub(r"ঘন্টা|ঘণ্টা", " hour ", text)
    return re.sub(r"\s+", " ", text)


def _split_findings(text: str, *, propagate_final_duration: bool = False) -> list[str]:
    # Newlines/semicolons/pipes are hard boundaries. The writer uses newlines
    # between Add-button rows, so duration propagation is limited to comma-
    # separated symptoms deliberately entered within the same row.
    groups = [
        group.strip(" .")
        for group in re.split(r"\s*(?:[;|\n]+)\s*", text)
        if group.strip(" .")
    ]
    findings: list[str] = []
    for group in groups:
        parts = [part.strip(" .") for part in re.split(r"\s*,+\s*", group) if part.strip(" .")]
        if not propagate_final_duration or len(parts) < 2:
            findings.extend(parts)
            continue
        final_match = _DURATION_AT_END.search(_duration_normalized_text(parts[-1]))
        if final_match:
            duration_text = final_match.group(0).strip()
            findings.extend(
                part if _DURATION_AT_END.search(_duration_normalized_text(part)) else f"{part} {duration_text}"
                for part in parts
            )
            continue
        active_duration = ""
        for part in parts:
            match = _DURATION_AT_END.search(_duration_normalized_text(part))
            if match:
                active_duration = match.group(0).strip()
                findings.append(part)
            elif active_duration:
                findings.append(f"{part} {active_duration}")
            else:
                findings.append(part)
    return findings


def _interpret_complaint(raw: str) -> dict[str, Any]:
    negated = re.match(r"(?i)^\s*(?:no|denies?|without)\s+(.+?)\s*$", raw)
    complaint_text = negated.group(1) if negated else raw
    status = "absent" if negated else "present"
    normalized = _duration_normalized_text(complaint_text)
    duration = re.search(
        r"\b(?:for\s+)?(\d+(?:\.\d+)?)\s*(hour|day|week|month|year)s?\b",
        normalized,
        re.IGNORECASE,
    )
    if duration:
        concept = complaint_text[: duration.start()].strip(" :-") or complaint_text
        numeric = float(duration.group(1))
        return _item(
            concept,
            status,
            "Chief_Complaint",
            raw,
            value=numeric if "." in duration.group(1) else int(numeric),
            unit=duration.group(2).lower() + ("s" if numeric != 1 else ""),
            interpretation="patient-reported complaint with duration",
        )
    return _item(
        complaint_text,
        status,
        "Chief_Complaint",
        raw,
        interpretation="patient-reported complaint",
    )


def _interpret_history(raw: str) -> dict[str, Any]:
    """Preserve free-text history as clinical evidence without diagnosing it."""
    item = _interpret_complaint(raw)
    item["source"] = "Past_History"
    item["interpretation"] = "patient-reported past medical history"
    return item


def _temperature(raw: str, source: str) -> dict[str, Any] | None:
    match = re.match(
        r"(?i)^\s*(?:temperature|temp)\s*[:=-]?\s*"
        r"(-?\d+(?:\.\d+)?)\s*(?:°\s*)?(f|c|fahrenheit|celsius)?\b",
        raw,
    )
    if not match:
        return None
    value = float(match.group(1))
    explicit_unit = (match.group(2) or "").lower()
    if explicit_unit.startswith("c"):
        unit, fever = "C", value >= 38.0
    elif explicit_unit.startswith("f"):
        unit, fever = "F", value >= 100.4
    else:
        unit = "F" if value > 60 else "C"
        fever = value >= (100.4 if unit == "F" else 38.0)
    return _item(
        "Temperature",
        "present" if fever else "normal",
        source,
        raw,
        value=value,
        unit=unit,
        interpretation="fever" if fever else "no fever by temperature threshold",
    )


def _pulse(raw: str, source: str) -> dict[str, Any] | None:
    match = re.match(
        r"(?i)^\s*(?:pulse(?:\s+rate)?)\s*[:=-]?\s*"
        r"(\d+(?:\.\d+)?)\s*(?:bpm|/\s*min(?:ute)?)?\b",
        raw,
    )
    if not match:
        return None
    value = float(match.group(1))
    return _item(
        "Pulse Rate",
        "recorded",
        source,
        raw,
        value=int(value) if value.is_integer() else value,
        unit="bpm",
    )


def _bmi(raw: str, source: str) -> dict[str, Any] | None:
    match = re.match(
        r"(?i)^\s*bmi\s*[:=-]?\s*(\d+(?:\.\d+)?)\s*(?:[-:/]\s*)?(.*)$",
        raw,
    )
    if not match:
        return None
    value = float(match.group(1))
    label_match = re.search(
        r"(?i)\b(obesity|obese|overweight|underweight)\b", match.group(2)
    )
    if label_match:
        label = label_match.group(1).lower()
        label = "obesity" if label == "obese" else label
        return _item(
            "BMI",
            "abnormal",
            source,
            raw,
            value=value,
            unit="kg/m²",
            interpretation=f"abnormal nutritional status: {label}",
        )
    return _item("BMI", "recorded", source, raw, value=value, unit="kg/m²")


def _exam_finding(raw: str, source: str) -> dict[str, Any]:
    special = _temperature(raw, source) or _pulse(raw, source) or _bmi(raw, source)
    if special:
        return special

    parenthesized = re.match(r"^\s*(.*?)\s*\(\s*([^()]*)\s*\)\s*$", raw)
    if parenthesized:
        concept = parenthesized.group(1).strip(" :-")
        marker = parenthesized.group(2).strip().lower()
        graded = re.match(r"^(\+{1,3})(?:\s+(mild|moderate|severe))?$", marker)
        severity = None
        if graded:
            status = "present"
            severity = graded.group(2) or {1: "mild", 2: "moderate", 3: "severe"}[len(graded.group(1))]
        elif marker.startswith("abnormal"):
            status = "abnormal"
        else:
            status = _STATUS_TERMS.get(marker, "recorded")
        return _item(
            concept or raw,
            status,
            source,
            raw,
            interpretation=marker if status == "recorded" else None,
            severity=severity,
        )

    labelled = re.match(r"^\s*(.*?)\s*[:=-]\s*(.*?)\s*$", raw)
    if labelled:
        concept = labelled.group(1).strip()
        marker = labelled.group(2).strip().lower()
        label_status = None
        if re.search(r"\b(high|low|raised|elevated|abnormal)\b", marker, re.IGNORECASE):
            label_status = "abnormal"
        elif re.search(r"\b(normal|nad|absent|negative)\b", marker, re.IGNORECASE):
            label_status = _STATUS_TERMS.get(marker, "normal")
        status = _STATUS_TERMS.get(marker, "recorded")
        if label_status:
            status = label_status
        return _item(
            concept or raw,
            status,
            source,
            raw,
            interpretation=marker if status in {"recorded", "abnormal"} else None,
        )

    marker = raw.strip().lower()
    suffix = re.match(r"^\s*(.*?)\s+\b(present|positive|absent|negative|normal|nad)\b\s*$", raw, re.IGNORECASE)
    if suffix:
        concept = suffix.group(1).strip(" .:-")
        marker = suffix.group(2).strip().lower()
        status = _STATUS_TERMS.get(marker, "recorded")
        return _item(
            concept or raw,
            status,
            source,
            raw,
            interpretation=marker if status == "recorded" else None,
        )
    return _item(raw, _STATUS_TERMS.get(marker, "recorded"), source, raw)


def interpret_clinical_findings(
    prescription: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return evidence from complaints, screening, exam, and result fields."""
    normalized: dict[str, Any] = {}
    for key, value in prescription.items():
        canonical = _ALIASES.get(str(key).strip(), str(key).strip())
        normalized[canonical] = value

    evidence: list[dict[str, Any]] = []
    for source in CLINICAL_SOURCES:
        text = _text(normalized.get(source, ""))
        if not text:
            continue
        for raw in _split_findings(text, propagate_final_duration=source == "Chief_Complaint"):
            if source == "Chief_Complaint":
                evidence.append(_interpret_complaint(raw))
            elif source == "Past_History":
                evidence.append(_interpret_history(raw))
            else:
                evidence.append(_exam_finding(raw, source))
    return evidence


__all__ = ["CLINICAL_SOURCES", "interpret_clinical_findings"]
