"""Central pre-parse language normalization for prescription text.

This module prepares traceable English/normalized hints before downstream
parsers and RAG routing. It does not decide audit errors.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .frequency_parser import normalize_digits
from .text_translation import translated_text

FIELDS_FOR_LANGUAGE_CONTEXT = (
    "Past_History",
    "Past History",
    "Chief_Complaint",
    "Chief Complaint",
    "Health_Screening",
    "Health Screening",
    "On_Examination",
    "On Examination",
    "Systemic_Examination",
    "Systemic Examination",
    "Investigation",
    "Investigation_Results",
    "Investigation Results",
    "Diagnosis",
    "Medication",
    "Advice",
)


def _text(value: Any, limit: int = 900) -> str:
    clean = " ".join(str(value or "").strip().split())
    return clean[:limit]


def normalize_prescription_language(
    prescription: Mapping[str, Any],
    clinical_evidence: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return field/evidence translation hints for parsers and RAG routing."""
    fields: dict[str, str] = {}
    for field in FIELDS_FOR_LANGUAGE_CONTEXT:
        value = _text(prescription.get(field, ""))
        if value:
            fields[field] = translated_text(normalize_digits(value))

    evidence: list[dict[str, Any]] = []
    for item in clinical_evidence or []:
        if not isinstance(item, Mapping):
            continue
        raw = _text(item.get("raw_text") or item.get("evidence") or item.get("concept"))
        normalized = _text(item.get("normalized_text") or translated_text(normalize_digits(raw)))
        evidence.append(
            {
                "source": _text(item.get("source", ""), 120),
                "concept": _text(item.get("concept", ""), 160),
                "status": _text(item.get("status", ""), 80),
                "raw_text": raw,
                "normalized_text": normalized,
                "interpretation": _text(item.get("interpretation", ""), 180),
                "value": item.get("value"),
                "unit": item.get("unit"),
            }
        )

    return {
        "fields": fields,
        "clinical_evidence": evidence,
    }


__all__ = ["normalize_prescription_language"]
