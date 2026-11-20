"""Candidate/intent-specific guideline RAG routing.

This module decides *where* to ask guideline RAG, without making final audit
decisions. It replaces broad section-level retrieval with smaller audit-intent
queries: diagnosis review, missing investigations, treatment review, avoid
medicine, and advice/follow-up.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .guideline_rag import GuidelineRAG, medicine_query_text
from .text_translation import translated_text

SECTIONS = ("Dx", "Inv", "Rx", "Adv")


@dataclass(frozen=True)
class GuidelineRAGSeed:
    section: str
    rule_name: str
    query_focus: str
    diagnosis: str = ""
    chief_complaint: str = ""
    clinical_text: str = ""
    medication_text: str = ""
    limit: int = 4


def _text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _field(record: Mapping[str, Any], *names: str) -> str:
    lowered = {str(key).strip().casefold(): value for key, value in record.items()}
    for name in names:
        value = _text(translated_text(lowered.get(name.casefold(), "")))
        if value and value.casefold() not in {"nan", "none", "null"}:
            return value
    return ""


def _raw_clinical_text(prescription: Mapping[str, Any]) -> str:
    return " ".join(
        _field(
            prescription,
            key,
        )
        for key in (
            "Health_Screening",
            "Health Screening",
            "On_Examination",
            "On Examination",
            "Systemic_Examination",
            "Systemic Examination",
            "Investigation_Results",
            "Investigation Results",
            "Advice",
        )
    )


def _context_text_for_rag(prescription: Mapping[str, Any]) -> str:
    """Keep outcome/context evidence without using ordered tests as symptoms.

    Investigation orders are the objects being audited. Including their names
    in the clinical retrieval query can make an unrelated test appear supported
    merely because a guideline mentions the same test. Actual results remain
    useful patient evidence and are retained.
    """
    return " ".join(
        _field(prescription, key)
        for key in (
            "Investigation_Results",
            "Investigation Results",
            "Advice",
        )
    )


def _clinical_text_from_evidence(clinical_evidence: Sequence[Mapping[str, Any]]) -> str:
    """Build RAG query text from only present/abnormal structured evidence.

    Raw prescription exam fields often contain terms like "No Dehydration",
    "Absent", "NAD", and "Normal". Passing those directly to RAG can retrieve
    dehydration or disease facts even though the finding is explicitly absent.
    """
    parts: list[str] = []
    for item in clinical_evidence:
        if not isinstance(item, Mapping):
            continue
        status = _text(item.get("status", "")).casefold()
        if status not in {"present", "abnormal"}:
            continue
        raw = _text(translated_text(item.get("raw_text") or item.get("evidence") or item.get("concept")))
        interpretation = _text(translated_text(item.get("interpretation", "")))
        if raw:
            parts.append(raw)
        if interpretation and interpretation.casefold() not in {
            "patient-reported complaint",
            "patient-reported complaint with duration",
        }:
            parts.append(interpretation)
    return " ".join(dict.fromkeys(part for part in parts if part))


def _clinical_text(
    prescription: Mapping[str, Any],
    clinical_evidence: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    if clinical_evidence is None:
        return _raw_clinical_text(prescription)
    return " ".join(
        part
        for part in (
            _clinical_text_from_evidence(clinical_evidence),
            _context_text_for_rag(prescription),
        )
        if part
    )


def _medicine_specific_text(medicine: Mapping[str, Any]) -> str:
    parts = []
    for key in ("raw_text", "brand_name", "generic_name", "dosage_form", "strength"):
        if medicine.get(key):
            parts.append(str(medicine[key]))
    for candidate in medicine.get("candidate_matches") or []:
        if isinstance(candidate, Mapping):
            for key in ("brand_name", "generic_name"):
                if candidate.get(key):
                    parts.append(str(candidate[key]))
    return " ".join(dict.fromkeys(part.strip() for part in parts if part.strip()))


def build_guideline_rag_seeds(
    prescription: Mapping[str, Any],
    normalized_medicines: Sequence[Mapping[str, Any]],
    clinical_evidence: Sequence[Mapping[str, Any]] | None = None,
) -> list[GuidelineRAGSeed]:
    """Build small retrieval intents from prescription structure.

    These are not final errors. They are RAG search intents. The downstream rule
    engine still decides whether a returned fact can become a candidate.
    """
    diagnosis = _field(prescription, "Diagnosis")
    chief_complaint = _field(prescription, "Chief_Complaint", "Chief Complaint")
    clinical = _clinical_text(prescription, clinical_evidence)
    raw_medication = _field(prescription, "Medication")
    seeds: list[GuidelineRAGSeed] = []

    if diagnosis or chief_complaint or clinical:
        seeds.extend(
            [
                GuidelineRAGSeed(
                    section="Dx",
                    rule_name="guideline_diagnosis_review_candidate",
                    query_focus="diagnosis support/differential/red flags",
                    diagnosis=diagnosis,
                    chief_complaint=chief_complaint,
                    clinical_text=clinical,
                    limit=4,
                ),
                GuidelineRAGSeed(
                    section="Inv",
                    rule_name="guideline_investigation_missing_candidate",
                    query_focus="recommended investigations/monitoring",
                    diagnosis=diagnosis,
                    chief_complaint=chief_complaint,
                    clinical_text=clinical,
                    limit=4,
                ),
                GuidelineRAGSeed(
                    section="Adv",
                    rule_name="guideline_advice_missing_candidate",
                    query_focus="recommended advice/referral/follow-up",
                    diagnosis=diagnosis,
                    chief_complaint=chief_complaint,
                    clinical_text=clinical,
                    limit=4,
                ),
            ]
        )

    for medicine in normalized_medicines:
        med_text = _medicine_specific_text(medicine)
        if not med_text:
            continue
        seeds.append(
            GuidelineRAGSeed(
                section="Rx",
                rule_name="guideline_treatment_review_candidate",
                query_focus="medicine choice/dose/frequency/duration for matched diagnosis",
                diagnosis=diagnosis,
                chief_complaint=chief_complaint,
                clinical_text=clinical,
                medication_text=med_text,
                limit=4,
            )
        )
        seeds.append(
            GuidelineRAGSeed(
                section="Rx",
                rule_name="guideline_avoid_medicine_candidate",
                query_focus="medicine avoidance/contraindication for matched diagnosis",
                diagnosis=diagnosis,
                chief_complaint=chief_complaint,
                clinical_text=clinical,
                medication_text=med_text,
                limit=3,
            )
        )

    if raw_medication and not normalized_medicines:
        seeds.append(
            GuidelineRAGSeed(
                section="Rx",
                rule_name="guideline_treatment_review_candidate",
                query_focus="medicine choice/dose/frequency/duration for matched diagnosis",
                diagnosis=diagnosis,
                chief_complaint=chief_complaint,
                clinical_text=clinical,
                medication_text=medicine_query_text(raw_medication, []),
                limit=4,
            )
        )
    return seeds


def retrieve_guideline_facts_for_seeds(
    rag: GuidelineRAG,
    seeds: Sequence[GuidelineRAGSeed],
) -> dict[str, Any]:
    grouped: dict[str, dict[str, Any]] = {
        section: {"facts": [], "matched_conditions": [], "warnings": [], "retrieval_seeds": []} for section in SECTIONS
    }
    seen_by_section: dict[str, set[str]] = {section: set() for section in SECTIONS}
    for seed in seeds:
        result = rag.retrieve(
            diagnosis=seed.diagnosis,
            chief_complaint=seed.chief_complaint,
            clinical_text=seed.clinical_text,
            medication_text=seed.medication_text,
            section=seed.section,
            limit=seed.limit,
        ).to_dict()
        grouped[seed.section]["retrieval_seeds"].append(
            {
                "rule_name": seed.rule_name,
                "query_focus": seed.query_focus,
                "medication_text": seed.medication_text,
                "fact_count": len(result.get("facts", []) or []),
            }
        )
        grouped[seed.section]["matched_conditions"].extend(result.get("matched_conditions", []) or [])
        grouped[seed.section]["warnings"].extend(result.get("warnings", []) or [])
        for fact in result.get("facts", []) or []:
            if not isinstance(fact, Mapping):
                continue
            fact_id = str(fact.get("fact_id") or repr(sorted(fact.items())))
            if fact_id in seen_by_section[seed.section]:
                continue
            item = dict(fact)
            item["retrieval_seed_rule_name"] = seed.rule_name
            item["retrieval_query_focus"] = seed.query_focus
            seen_by_section[seed.section].add(fact_id)
            grouped[seed.section]["facts"].append(item)
    for section in SECTIONS:
        grouped[section]["matched_conditions"] = sorted(set(grouped[section]["matched_conditions"]))
        grouped[section]["warnings"] = sorted(set(grouped[section]["warnings"]))
    return grouped


__all__ = [
    "GuidelineRAGSeed",
    "build_guideline_rag_seeds",
    "retrieve_guideline_facts_for_seeds",
]
