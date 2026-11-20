"""Assemble one validated final audit report per prescription."""

from __future__ import annotations

import os
import re
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from .models import Finding
from .reconcile import reconcile


SECTIONS = ("Demo", "CC", "Hx", "Dx", "Inv", "Rx", "Adv")
_REQUIRED_FINDING_FIELDS = (
    "section",
    "rule_name",
    "rule_id",
    "why",
    "correction",
    "evidence",
    "confidence",
)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_safe(item) for item in value]
    return str(value)


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _confidence_number(value: Any) -> float:
    if isinstance(value, (int, float)):
        return min(1.0, max(0.0, float(value)))
    return {"low": 0.3, "medium": 0.6, "high": 0.9}.get(
        _text(value).casefold(), 0.5
    )


def _concern_level(value: Any, *, rule_id: str = "", rule_name: str = "") -> str:
    text = _text(value).casefold().replace("_", " ")
    if text in {"critical", "major", "moderate", "minor"}:
        return text
    combined = f"{rule_id} {rule_name}".casefold()
    if any(
        marker in combined
        for marker in (
            "contraindication",
            "allergy",
            "ddi",
            "dose.max",
            "pregnancy",
            "hepatic",
            "renal",
        )
    ):
        return "critical"
    if any(
        marker in combined
        for marker in ("dose.high", "dose.weight", "strength.mismatch", "rag.avoid")
    ):
        return "major"
    if rule_id.casefold().startswith(("cc.", "demo.", "hx.")):
        return "minor"
    return "moderate"


def _concern_number(value: Any) -> int:
    return {"minor": 1, "moderate": 2, "major": 3, "critical": 4}.get(
        _text(value).casefold(), 2
    )


def _join_unique_text(values: Sequence[str], separator: str = " | ") -> str:
    return separator.join(dict.fromkeys(_text(value) for value in values if _text(value)))


def _safety_similarity_key(finding: Mapping[str, Any], section: str, rule_id: str) -> tuple[str, ...] | None:
    generic_id = _text(finding.get("generic_id", ""))
    patient_risk_type = _text(finding.get("patient_risk_type", ""))
    if not generic_id or not patient_risk_type:
        return None
    return (
        section.casefold(),
        rule_id.casefold(),
        _text(finding.get("rule_name", "")).casefold(),
        generic_id.casefold(),
        patient_risk_type.casefold(),
    )


def _merge_validated_finding(existing: dict[str, Any], incoming: Mapping[str, Any]) -> None:
    existing["why"] = _join_unique_text(
        [str(existing.get("why", "")), _text(incoming.get("why", ""))]
    )
    existing["correction"] = _join_unique_text(
        [str(existing.get("correction", "")), _text(incoming.get("correction", ""))]
    )
    existing["evidence"] = _join_unique_text(
        [str(existing.get("evidence", "")), _text(incoming.get("evidence", ""))]
    )
    if _confidence_number(incoming.get("confidence")) > _confidence_number(existing.get("confidence")):
        existing["confidence"] = _json_safe(incoming.get("confidence"))
    incoming_level = _concern_level(
        incoming.get("concern_level"),
        rule_id=_text(incoming.get("rule_id", "")),
        rule_name=_text(incoming.get("rule_name", "")),
    )
    if _concern_number(incoming_level) > _concern_number(existing.get("concern_level")):
        existing["concern_level"] = incoming_level


def _split_evidence_items(value: Any) -> list[str]:
    text = _text(value)
    if not text:
        return []
    parts = re.split(r"\s*(?:;|\n|\|)\s*", text)
    return [_text(part).strip(" .:-") for part in parts if _text(part).strip(" .:-")]


def _dedupe_evidence_text(value: Any) -> str:
    parts = _split_evidence_items(value)
    if len(parts) <= 1:
        return _text(value)
    return " | ".join(dict.fromkeys(parts))


def _group_cc_findings(grouped: dict[str, list[dict[str, Any]]]) -> None:
    """Fold repeated same-rule CC findings into one human-readable finding."""
    cc_findings = grouped.get("CC", [])
    if len(cc_findings) < 2:
        return
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
    passthrough: list[dict[str, Any]] = []
    for item in cc_findings:
        key = (_text(item.get("rule_id", "")).casefold(), _text(item.get("rule_name", "")).casefold())
        if key[0] and key[1]:
            buckets.setdefault(key, []).append(item)
        else:
            passthrough.append(item)

    grouped_cc: list[dict[str, Any]] = []
    for items in buckets.values():
        if len(items) == 1:
            grouped_cc.append(items[0])
            continue
        first = dict(items[0])
        complaints: list[str] = []
        for item in items:
            complaints.extend(_split_evidence_items(item.get("evidence", "")))
        complaints = list(dict.fromkeys(complaints))
        if not complaints:
            grouped_cc.extend(items)
            continue
        rule_id = _text(first.get("rule_id", ""))
        rule_name = _text(first.get("rule_name", ""))
        evidence_semicolon = "; ".join(complaints)
        evidence_comma = ", ".join(complaints)
        if rule_id.casefold() == "cc.3" or "duration" in rule_name.casefold():
            first["why"] = "Duration is not documented for multiple patient-reported complaints."
            first["correction"] = f"Document duration for: {evidence_comma}."
        elif rule_id.casefold() == "cc.2" or "severity" in rule_name.casefold():
            first["why"] = "Severity is not documented for multiple pain complaints."
            first["correction"] = f"Document severity for: {evidence_comma}."
        else:
            first["why"] = _join_unique_text([str(item.get("why", "")) for item in items])
            first["correction"] = _join_unique_text(
                [str(item.get("correction", "")) for item in items]
            )
        first["evidence"] = evidence_semicolon
        strongest = max(items, key=lambda item: _confidence_number(item.get("confidence")))
        first["confidence"] = _json_safe(strongest.get("confidence"))
        grouped_cc.append(first)
    grouped["CC"] = passthrough + grouped_cc


def _unwrap_candidates(
    value: Mapping[str, Any],
) -> tuple[Mapping[str, Sequence[Mapping[str, Any]]], list[str]]:
    if "candidates_by_section" in value:
        grouped = value.get("candidates_by_section", {})
        warnings = [_text(item) for item in value.get("warnings", []) if _text(item)]
        return grouped if isinstance(grouped, Mapping) else {}, warnings
    return value, []


def _final_rule_name(rule_name: Any) -> str:
    name = _text(rule_name)
    return name[:-10] if name.endswith("_candidate") else name


def _flatten_candidates(
    candidate_groups: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for section in SECTIONS:
        for candidate in candidate_groups.get(section, []) or []:
            if isinstance(candidate, Mapping):
                item = dict(candidate)
                item.setdefault("section", section)
                result.append(item)
    return result



def _candidate_importance_counts(candidate_groups: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict[str, int]:
    counts = {"critical_review": 0, "moderate_review": 0, "minor_documentation": 0, "review": 0}
    for candidate in _flatten_candidates(candidate_groups):
        importance = _text(candidate.get("importance", "")) or _text(candidate.get("evidence_strength", "")) or "review"
        if importance == "high":
            importance = "critical_review"
        elif importance == "medium":
            importance = "moderate_review"
        elif importance == "low":
            importance = "minor_documentation"
        if importance not in counts:
            counts[importance] = 0
        counts[importance] += 1
    return counts

def _finding_match_keys(
    errors_by_section: Mapping[str, Sequence[Mapping[str, Any]]],
) -> set[tuple[str, str, str]]:
    keys: set[tuple[str, str, str]] = set()
    for section in SECTIONS:
        for finding in errors_by_section.get(section, []) or []:
            keys.add(
                (
                    section,
                    _text(finding.get("rule_id", "")).casefold(),
                    _text(finding.get("evidence", "")).casefold(),
                )
            )
            keys.add(
                (
                    section,
                    _final_rule_name(finding.get("rule_name", "")).casefold(),
                    _text(finding.get("evidence", "")).casefold(),
                )
            )
    return keys


def _rejected_high_evidence_candidates(
    candidate_groups: Mapping[str, Sequence[Mapping[str, Any]]],
    errors_by_section: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    finding_keys = _finding_match_keys(errors_by_section)
    rejected: list[dict[str, Any]] = []
    for candidate in _flatten_candidates(candidate_groups):
        if _text(candidate.get("evidence_strength", "")).casefold() != "high":
            continue
        if candidate.get("must_review_if_rejected") is not True:
            continue
        section = _text(candidate.get("section", ""))
        rule_id = _text(candidate.get("rule_id", ""))
        rule_name = _final_rule_name(candidate.get("rule_name", ""))
        evidence = _text(candidate.get("evidence", ""))
        accepted = (
            (section, rule_id.casefold(), evidence.casefold()) in finding_keys
            or (section, rule_name.casefold(), evidence.casefold()) in finding_keys
        )
        if accepted:
            continue
        rejected.append(
            {
                "section": section,
                "rule_id": rule_id,
                "rule_name": rule_name,
                "evidence_strength": _text(candidate.get("evidence_strength", "")),
                "generic_id": _text(candidate.get("generic_id", "")),
                "generic_name": _text(candidate.get("generic_name", "")),
                "evidence": evidence,
                "why_candidate": _text(candidate.get("why_candidate", "")),
                "needs_human_review": True,
            }
        )
    return rejected


def _unwrap_section_outputs(
    value: Mapping[str, Any],
) -> tuple[Mapping[str, Sequence[Mapping[str, Any]]], list[str]]:
    if "section_outputs" in value:
        outputs = value.get("section_outputs", {})
        warnings = [_text(item) for item in value.get("warnings", []) if _text(item)]
        return outputs if isinstance(outputs, Mapping) else {}, warnings
    return value, []


def _validate_findings(
    outputs: Mapping[str, Sequence[Mapping[str, Any]]],
    warnings: list[str],
) -> tuple[dict[str, list[dict[str, Any]]], bool]:
    grouped: dict[str, list[dict[str, Any]]] = {section: [] for section in SECTIONS}
    seen: set[tuple[str, str, str]] = set()
    safety_seen: dict[tuple[str, ...], dict[str, Any]] = {}
    explicit_review = False
    for output_section, findings in outputs.items():
        if output_section not in SECTIONS:
            warnings.append(f"unknown section output was dropped: {output_section}")
            continue
        if not isinstance(findings, Sequence) or isinstance(findings, (str, bytes)):
            warnings.append(f"section {output_section} output is not a findings list")
            continue
        for index, finding in enumerate(findings):
            if not isinstance(finding, Mapping):
                warnings.append(f"section {output_section} finding {index} is not an object and was dropped")
                continue
            section = _text(finding.get("section", ""))
            if section not in SECTIONS:
                warnings.append(
                    f"section {output_section} finding {index} has unknown section {section!r} and was dropped"
                )
                continue
            if section != output_section:
                warnings.append(
                    f"finding section {section} does not match output section {output_section}; finding was dropped"
                )
                continue
            missing = [
                field
                for field in _REQUIRED_FINDING_FIELDS
                if field not in finding or finding.get(field) is None
            ]
            if missing:
                warnings.append(
                    f"section {section} finding {index} missing required fields "
                    f"{', '.join(missing)} and was dropped"
                )
                continue
            evidence = _dedupe_evidence_text(finding.get("evidence", ""))
            if not evidence:
                warnings.append(
                    f"section {section} finding {index} has no evidence and was dropped"
                )
                continue
            rule_name = _text(finding.get("rule_name", ""))
            rule_id = _text(finding.get("rule_id", ""))
            why = _text(finding.get("why", ""))
            correction = _text(finding.get("correction", ""))
            if not rule_name or not rule_id or not why or not correction:
                warnings.append(
                    f"section {section} finding {index} has empty required text and was dropped"
                )
                continue
            if (
                _text(finding.get("confidence", "")).casefold() == "needs_review"
                or _text(finding.get("severity", "")).casefold() == "needs_review"
            ):
                explicit_review = True
            safety_key = _safety_similarity_key(finding, section, rule_id)
            if safety_key and safety_key in safety_seen:
                _merge_validated_finding(safety_seen[safety_key], finding)
                continue
            key = section, rule_id.casefold(), evidence.casefold()
            if key in seen:
                continue
            seen.add(key)
            item = {
                "candidate_id": _text(finding.get("candidate_id", "")),
                "section": section,
                "rule_name": rule_name,
                "rule_id": rule_id,
                "why": why,
                "correction": correction,
                "evidence": evidence,
                "confidence": _json_safe(finding.get("confidence")),
                "concern_level": _concern_level(
                    finding.get("concern_level"),
                    rule_id=rule_id,
                    rule_name=rule_name,
                ),
            }
            grouped[section].append(item)
            if safety_key:
                safety_seen[safety_key] = item
    _group_cc_findings(grouped)
    return grouped, explicit_review


def _consolidated_summary(errors_by_section: Mapping[str, Sequence[Mapping[str, Any]]]) -> list[str]:
    models = []
    for section in SECTIONS:
        for item in errors_by_section[section]:
            models.append(
                Finding(
                    section=section,
                    concern_id=str(item["rule_id"]),
                    evidence=str(item["evidence"]),
                    why=str(item["why"]),
                    correction=str(item["correction"]),
                    confidence=_confidence_number(item["confidence"]),
                )
            )
    if not models:
        return []
    _, summary = reconcile(models, SECTIONS)
    return summary


def assemble_final_report(
    prescription_id: Any,
    prescription: Mapping[str, Any],
    clinical_evidence: Sequence[Mapping[str, Any]],
    patient_context: Mapping[str, Any],
    normalized_medicines: Sequence[Mapping[str, Any]],
    medicine_facts: Sequence[Mapping[str, Any]],
    followup_context: Mapping[str, Any],
    candidates_by_section: Mapping[str, Any],
    section_outputs: Mapping[str, Any],
    *,
    include_debug: bool = False,
    stage_warnings: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Validate, deduplicate, reconcile, and assemble the final report."""
    candidate_groups, candidate_warnings = _unwrap_candidates(candidates_by_section)
    output_groups, writer_warnings = _unwrap_section_outputs(section_outputs)
    warnings = list(candidate_warnings)
    warnings.extend(writer_warnings)
    warnings.extend(_text(item) for item in stage_warnings or [] if _text(item))
    warnings.extend(
        _text(item) for item in patient_context.get("warnings", []) if _text(item)
    )
    warnings.extend(
        _text(item) for item in followup_context.get("warnings", []) if _text(item)
    )
    for fact_result in medicine_facts:
        if isinstance(fact_result, Mapping):
            warnings.extend(
                _text(item) for item in fact_result.get("warnings", []) if _text(item)
            )

    errors_by_section, explicit_review = _validate_findings(output_groups, warnings)
    rejected_high_evidence = _rejected_high_evidence_candidates(
        candidate_groups, errors_by_section
    )
    rag_gaps = [
        _json_safe(item)
        for item in candidates_by_section.get("rag_gaps", [])
        if isinstance(item, Mapping)
    ]
    warnings = list(dict.fromkeys(warnings))
    candidate_count = sum(
        len(candidate_groups.get(section, []) or []) for section in SECTIONS
    )
    final_finding_count = sum(len(errors_by_section[section]) for section in SECTIONS)
    candidate_importance_counts = _candidate_importance_counts(candidate_groups)
    if isinstance(section_outputs, Mapping) and isinstance(section_outputs.get("model_calls"), Sequence):
        model_sections_called = list(
            dict.fromkeys(
                _text(call.get("section", ""))
                for call in section_outputs.get("model_calls", [])
                if isinstance(call, Mapping) and _text(call.get("section", "")) in SECTIONS
            )
        )
    else:
        model_sections_called = [section for section in SECTIONS if section in output_groups]
    skipped_sections = [
        {"section": section, "reason": "no_candidates"}
        for section in SECTIONS
        if not candidate_groups.get(section, []) and section not in output_groups
    ]
    if writer_warnings:
        status = "partial_failed"
    # Model only (AUDIT_MODEL_ONLY, default on): candidates the model rejected stay in metadata for inspection but
    # do not mark the prescription as needing review.
    elif explicit_review or (rejected_high_evidence and (os.getenv("AUDIT_MODEL_ONLY") or "1").strip().casefold()
                                                          in {"0", "false", "no", "off"}):
        status = "needs_review"
    else:
        status = "completed"

    report: dict[str, Any] = {
        "prescription_id": str(prescription_id),
        "status": status,
        "errors_by_section": errors_by_section,
        "consolidated_summary": _consolidated_summary(errors_by_section),
        "metadata": {
            "candidate_count": candidate_count,
            "final_finding_count": final_finding_count,
            "candidate_importance_counts": candidate_importance_counts,
            "model_sections_called": model_sections_called,
            "skipped_sections": skipped_sections,
            "warnings": warnings,
            "rejected_high_evidence_candidates": rejected_high_evidence,
            "rag_gaps": rag_gaps,
        },
    }
    if include_debug:
        report["debug"] = {
            "clinical_evidence": _json_safe(clinical_evidence),
            "normalized_medicines": _json_safe(normalized_medicines),
            "followup_context": _json_safe(followup_context),
            "medicine_facts": _json_safe(medicine_facts),
        }
    return report


__all__ = ["SECTIONS", "assemble_final_report"]
