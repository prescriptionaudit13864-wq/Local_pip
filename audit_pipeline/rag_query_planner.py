"""Optional LLM-assisted guideline RAG query planning.

This module does not judge prescriptions and does not create final findings.
It only rewrites deterministic guideline RAG seeds into tighter retrieval text.
If planning fails or returns unsafe/empty output, callers keep the original
deterministic seeds.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from .guideline_rag_router import GuidelineRAGSeed
from .provider import parse_json_value

SYSTEM_PROMPT = """You are a retrieval query planner for a prescription audit system.
Your only job is to improve RAG search terms. Do not decide whether there is an error.
Return JSON only. Never include API keys, environment values, or hidden reasoning."""


def _text(value: Any, limit: int = 900) -> str:
    clean = " ".join(str(value or "").strip().split())
    return clean[:limit]


def _seed_payload(index: int, seed: GuidelineRAGSeed) -> dict[str, Any]:
    return {
        "index": index,
        "section": seed.section,
        "rule_name": seed.rule_name,
        "query_focus": seed.query_focus,
        "diagnosis": _text(seed.diagnosis),
        "chief_complaint": _text(seed.chief_complaint),
        "clinical_text": _text(seed.clinical_text),
        "medication_text": _text(seed.medication_text),
        "limit": seed.limit,
    }


def build_planner_prompt(
    prescription: Mapping[str, Any],
    seeds: Sequence[GuidelineRAGSeed],
    clinical_evidence: Sequence[Mapping[str, Any]] | None = None,
    language_context: Mapping[str, Any] | None = None,
) -> str:
    evidence_payload: list[dict[str, Any]] = []
    for item in clinical_evidence or []:
        if not isinstance(item, Mapping):
            continue
        evidence_payload.append(
            {
                "source": _text(item.get("source", ""), 120),
                "concept": _text(item.get("concept", ""), 160),
                "status": _text(item.get("status", ""), 80),
                "raw_text": _text(item.get("raw_text", ""), 220),
                "normalized_text": _text(item.get("normalized_text", ""), 260),
                "interpretation": _text(item.get("interpretation", ""), 180),
                "value": item.get("value"),
                "unit": item.get("unit"),
            }
        )
    payload = {
        "instruction": (
            "For each seed, return tighter retrieval fields for guideline RAG. "
            "Use only visible prescription text and structured clinical evidence. "
            "Translate non-English clinical terms into concise English retrieval terms when clear. "
            "Keep important diagnosis, symptom, exam, lab, patient-risk, medicine, dose, route, "
            "and advice terms. Investigation orders are the objects being audited, not evidence "
            "for a disease or for their own relevance; never infer clinical support from an "
            "ordered test name. Investigation results may be used as patient evidence. "
            "Do not invent facts. Do not judge the prescription."
        ),
        "output_schema": {
            "seeds": [
                {
                    "index": 0,
                    "diagnosis": "focused diagnosis/disease terms",
                    "chief_complaint": "focused symptom terms",
                    "clinical_text": "focused clinical/lab/exam/advice terms",
                    "medication_text": "focused medicine/generic/dose terms",
                    "planning_reason": "short retrieval reason, not clinical judgment",
                }
            ]
        },
        "prescription": {
            "diagnosis": _text(prescription.get("Diagnosis", "")),
            "chief_complaint": _text(
                prescription.get("Chief_Complaint", "") or prescription.get("Chief Complaint", "")
            ),
            "health_screening": _text(
                prescription.get("Health_Screening", "") or prescription.get("Health Screening", "")
            ),
            "on_examination": _text(prescription.get("On_Examination", "") or prescription.get("On Examination", "")),
            "systemic_examination": _text(
                prescription.get("Systemic_Examination", "") or prescription.get("Systemic Examination", "")
            ),
            "investigation_results": _text(
                prescription.get("Investigation_Results", "") or prescription.get("Investigation Results", "")
            ),
            "medication": _text(prescription.get("Medication", "")),
            "advice": _text(prescription.get("Advice", "")),
        },
        "structured_clinical_evidence": evidence_payload,
        "preparse_language_context": language_context or {},
        "seeds": [_seed_payload(index, seed) for index, seed in enumerate(seeds)],
    }
    return "RAG_QUERY_PLANNING_JSON=" + json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _planned_seed(
    original: GuidelineRAGSeed,
    item: Mapping[str, Any],
) -> GuidelineRAGSeed:
    diagnosis = _text(item.get("diagnosis", original.diagnosis))
    chief_complaint = _text(item.get("chief_complaint", original.chief_complaint))
    clinical_text = _text(item.get("clinical_text", original.clinical_text))
    medication_text = _text(item.get("medication_text", original.medication_text))
    if not any((diagnosis, chief_complaint, clinical_text, medication_text)):
        return original
    return replace(
        original,
        diagnosis=diagnosis or original.diagnosis,
        chief_complaint=chief_complaint or original.chief_complaint,
        clinical_text=clinical_text or original.clinical_text,
        medication_text=medication_text or original.medication_text,
    )


def plan_guideline_rag_seeds(
    *,
    prescription: Mapping[str, Any],
    seeds: Sequence[GuidelineRAGSeed],
    provider: Any,
    clinical_evidence: Sequence[Mapping[str, Any]] | None = None,
    language_context: Mapping[str, Any] | None = None,
    max_tokens: int = 1200,
) -> dict[str, Any]:
    """Return planned seeds and compact trace data.

    On provider/model/parse failure the original seeds are returned with a warning.
    """
    original = list(seeds)
    if not original:
        return {"seeds": [], "warnings": [], "plans": [], "model_call": None}
    try:
        raw = provider.audit(
            system=SYSTEM_PROMPT,
            prompt=build_planner_prompt(prescription, original, clinical_evidence, language_context),
            max_tokens=max_tokens,
        )
        parsed = parse_json_value(raw)
    except Exception as exc:
        return {
            "seeds": original,
            "warnings": [f"llm_rag_router failed; deterministic seeds used: {type(exc).__name__}"],
            "plans": [],
            "model_call": {"success": False, "error_type": type(exc).__name__},
        }
    if not isinstance(parsed, Mapping):
        return {
            "seeds": original,
            "warnings": ["llm_rag_router returned non-object JSON; deterministic seeds used"],
            "plans": [],
            "model_call": {"success": False, "error_type": "invalid_json_shape"},
        }
    planned_by_index: dict[int, Mapping[str, Any]] = {}
    for item in parsed.get("seeds", []) or []:
        if not isinstance(item, Mapping):
            continue
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        if 0 <= index < len(original):
            planned_by_index[index] = item
    planned_seeds: list[GuidelineRAGSeed] = []
    plans: list[dict[str, Any]] = []
    for index, seed in enumerate(original):
        planned_item = planned_by_index.get(index)
        if planned_item is None:
            planned = seed
            reason = ""
        else:
            planned = _planned_seed(seed, planned_item)
            reason = _text(planned_item.get("planning_reason", ""), 240)
        planned_seeds.append(planned)
        plans.append(
            {
                "index": index,
                "section": seed.section,
                "rule_name": seed.rule_name,
                "changed": planned != seed,
                "planning_reason": reason,
                "original": _seed_payload(index, seed),
                "planned": _seed_payload(index, planned),
            }
        )
    return {
        "seeds": planned_seeds,
        "warnings": [],
        "plans": plans,
        "model_call": {
            "success": True,
            "planned_seed_count": len(planned_by_index),
        },
    }


__all__ = ["build_planner_prompt", "plan_guideline_rag_seeds"]
