"""Configuration and compact responses for PrescribeX live section audits.

The React writer has seven completion steps, six of which own an audit badge.
Examination supplies structured context without a separate audit domain, and
follow-up is assessed as part of the Advice domain.
"""

from __future__ import annotations

import os
import re
from typing import Any, Mapping

from .patient_context import CLINICAL_WARNING_MESSAGES, MALE_PREGNANCY_WARNING

_DEMOGRAPHIC_RULES = {"CC.context.demo", "CC.demo"}


SECTION_PROFILES: dict[str, tuple[str, ...]] = {
    "chiefComplaints": ("CC",),
    "pastHistory": ("Hx",),
    "investigations": ("Inv",),
    "diagnosis": ("Dx",),
    "rx": ("Rx",),
    "advice": ("Adv",),
}

# Current-visit data allowed into each live audit. This is the authoritative
# writer dependency matrix. Demographics are carried in the separate patient
# envelope, and prior encounters remain available through the follow-up
# database. A Diagnosis save may refresh a completed Investigation check after
# the Diagnosis result returns, so the two model calls never compete.
LIVE_SECTION_RECORD_FIELDS: dict[str, tuple[str, ...]] = {
    "chiefComplaints": (
        "chiefComplaints",
        "ancPncNotes",
        "healthScreening",
        "investigationResults",
        "heightFeet",
        "heightInch",
        "weight",
        "bmi",
        "bsa",
    ),
    "pastHistory": (
        "chiefComplaints",
        "pastHistory",
        "ancPncNotes",
        "healthScreening",
        "onExamination",
        "systemicExamination",
        "investigationResults",
        "heightFeet",
        "heightInch",
        "weight",
        "bmi",
        "bsa",
    ),
    "investigations": (
        "chiefComplaints",
        "pastHistory",
        "ancPncNotes",
        "healthScreening",
        "onExamination",
        "systemicExamination",
        "investigationResults",
        "investigations",
        "diagnosis",
        "heightFeet",
        "heightInch",
        "weight",
        "bmi",
        "bsa",
    ),
    "diagnosis": (
        "chiefComplaints",
        "pastHistory",
        "ancPncNotes",
        "healthScreening",
        "onExamination",
        "systemicExamination",
        "investigationResults",
        "investigations",
        "diagnosis",
        "heightFeet",
        "heightInch",
        "weight",
        "bmi",
        "bsa",
    ),
    "rx": (
        "chiefComplaints",
        "pastHistory",
        "ancPncNotes",
        "healthScreening",
        "onExamination",
        "systemicExamination",
        "investigationResults",
        "investigations",
        "diagnosis",
        "rxList",
        "heightFeet",
        "heightInch",
        "weight",
        "bmi",
        "bsa",
    ),
    "advice": (
        "chiefComplaints",
        "pastHistory",
        "ancPncNotes",
        "healthScreening",
        "onExamination",
        "systemicExamination",
        "investigationResults",
        "investigations",
        "diagnosis",
        "rxList",
        "adviceList",
        "followUps",
        "heightFeet",
        "heightInch",
        "weight",
        "bmi",
        "bsa",
    ),
}

_LEVEL_RANK = {"minor": 1, "moderate": 2, "major": 3, "critical": 4}


def _live_failure_summary(warnings: list[str]) -> str:
    combined = " ".join(str(item) for item in warnings).casefold()
    if "model_permission_blocked_project" in combined or "blocked at the project level" in combined:
        return (
            "Groq has blocked the configured model for this project. Enable a text model "
            "in Groq project settings (or use an enabled project key), restart the audit "
            "server, and run this check again."
        )
    if "authenticationerror" in combined or "invalid_api_key" in combined:
        return "The audit provider rejected the API key. Update the key, restart the audit server, and retry."
    if "ratelimiterror" in combined or "rate limit" in combined or "quota" in combined:
        return "The audit provider limit was reached. Wait or update the provider limit, then retry."
    return "The model provider could not complete this live audit. Check the pipeline note and run it again."


def audit_domains_for(section: Any) -> tuple[str, ...]:
    """Return V3 domains for a supported writer section."""
    key = str(section or "").strip()
    try:
        return SECTION_PROFILES[key]
    except KeyError as exc:
        allowed = ", ".join(SECTION_PROFILES)
        raise ValueError(f"unsupported live-audit section {key!r}; use one of: {allowed}") from exc


def scope_live_record(section: Any, record: Mapping[str, Any]) -> dict[str, Any]:
    """Return only the current-visit fields permitted for one live audit."""
    key = str(section or "").strip()
    audit_domains_for(key)  # validate with the same public error message
    allowed = {"id", "date", "dateISO", *LIVE_SECTION_RECORD_FIELDS[key]}
    return {name: value for name, value in record.items() if name in allowed}


def summarize_live_report(
    section: str,
    report: Mapping[str, Any],
    *,
    revision: Any = None,
) -> dict[str, Any]:
    """Reduce a normal V3 report to the badge/details contract."""
    domains = audit_domains_for(section)
    grouped = report.get("errors_by_section", {}) if isinstance(report, Mapping) else {}
    findings = [
        dict(item)
        for domain in domains
        for item in (grouped.get(domain, []) or [])
        if isinstance(item, Mapping)
    ]
    accepted_rule_keys = {
        (
            str(item.get("section", "")).strip().casefold(),
            str(item.get("rule_id", "")).strip().casefold(),
        )
        for item in findings
        if str(item.get("section", "")).strip() and str(item.get("rule_id", "")).strip()
    }
    metadata = report.get("metadata", {}) or {}
    all_warnings = [str(item) for item in (metadata.get("warnings", []) or [])]
    # Patient-context notes such as a male patient with pregnancy evidence are likely errors in the
    # prescription itself: show them as "possible errors" and count them as concerns. They concern the
    # demographics, so only the chief-complaint check shows them, not every section.
    # The rest are pipeline/technical notes.
    # Model only (default since 2026-11-14, the user's decision): alerts come from the model's own decisions. The
    # hand-written patient-context notes are not shown as errors, and candidates the model rejected are not put
    # back. AUDIT_MODEL_ONLY=0 restores both.
    model_only = (os.getenv("AUDIT_MODEL_ONLY") or "1").strip().casefold() not in {"0", "false", "no", "off"}
    possible_errors = (
        list(dict.fromkeys(
            CLINICAL_WARNING_MESSAGES[item] for item in all_warnings if item in CLINICAL_WARNING_MESSAGES
        ))
        if "CC" in domains and not model_only
        else []
    )
    # The male-pregnancy note restates a complaint-vs-sex finding the audit already shows on the exact line:
    # showing both put the same problem on screen twice.
    if any(str(item.get("rule_id", "")) in _DEMOGRAPHIC_RULES for item in findings):
        possible_errors = [text for text in possible_errors if text != CLINICAL_WARNING_MESSAGES.get(MALE_PREGNANCY_WARNING)]
    warnings = [item for item in all_warnings if item not in CLINICAL_WARNING_MESSAGES]
    rejected = [
        dict(item)
        for item in (metadata.get("rejected_high_evidence_candidates", []) or [])
        if (
            not model_only
            and isinstance(item, Mapping)
            and str(item.get("section", "")) in domains
            and (
                str(item.get("section", "")).strip().casefold(),
                str(item.get("rule_id", "")).strip().casefold(),
            ) not in accepted_rule_keys
        )
    ]
    if rejected:
        findings.extend(
            {
                "section": item.get("section", ""),
                "rule_name": item.get("rule_name", "candidate_needs_review"),
                "rule_id": item.get("rule_id", ""),
                "why": item.get("why_candidate", "A high-evidence candidate requires clinician review."),
                "correction": "Review the candidate and its supporting evidence.",
                "evidence": item.get("evidence", ""),
                "confidence": "needs_review",
                "concern_level": "moderate",
            }
            for item in rejected
        )

    # Error fatigue: show the most important concerns first and cap the list. The complete list
    # stays in the final audit report.
    total_findings = len(findings)
    findings.sort(key=lambda item: (
        -_LEVEL_RANK.get(str(item.get("concern_level", "")).casefold(), 2),
        {"high": 0, "medium": 1, "needs_review": 1, "low": 2}.get(str(item.get("confidence", "")).casefold(), 1),
    ))
    limit = max(1, int(os.getenv("LIVE_AUDIT_MAX_FINDINGS") or 5))
    findings = findings[:limit]

    if report.get("status") == "partial_failed":
        status = "error"
        summary = _live_failure_summary(warnings)
    elif not findings and not possible_errors and warnings:
        status = "moderate"
        summary = "The live audit completed with pipeline notes that require review."
    elif not findings and not possible_errors:
        status = "clear"
        summary = "No anomaly detected from the available evidence."
    else:
        strongest = max(
            (_LEVEL_RANK.get(str(item.get("concern_level", "")).casefold(), 2) for item in findings),
            default=2,
        )
        status = "critical" if strongest >= _LEVEL_RANK["major"] else "moderate"
        total_concerns = total_findings + len(possible_errors)
        shown = len(findings) + len(possible_errors)
        if total_concerns == 1:
            summary = "1 concern requires review."
        elif total_concerns > shown:
            summary = f"{total_concerns} concerns found; the {shown} most important are shown (the final AI Audit lists all)."
        else:
            summary = f"{total_concerns} concerns require review."

    return {
        "section": section,
        "domains": list(domains),
        "revision": revision,
        "status": status,
        "summary": summary,
        "findings": findings,
        "possible_errors": possible_errors,
        "warnings": warnings,
    }


def _finding_identity(item: Mapping[str, Any]) -> tuple[str, ...]:
    """Build a stable identity shared by live and final audit findings."""
    section = str(item.get("section", "") or "").strip().casefold()
    rule_id = str(item.get("rule_id", "") or "").strip().casefold()
    candidate_id = str(item.get("candidate_id", "") or "").strip().casefold()
    if candidate_id:
        return section, rule_id, candidate_id

    # Older saved live checks do not contain candidate_id.  The first evidence
    # clause normally identifies the audited complaint, investigation, medicine,
    # or clinical finding and is stable even when later context becomes richer.
    evidence = str(item.get("evidence", "") or "").split("|", 1)[0]
    evidence = re.sub(r"\s+", " ", evidence).strip().casefold()
    evidence = re.sub(
        r"^(?:ordered(?: investigation)?|entered investigations?|medicine|prescribed|"
        r"diagnosis|chief complaint|patient evidence|evidence)\s*:\s*",
        "",
        evidence,
    )
    return section, rule_id, evidence


def compare_live_with_final(
    live_audits: Any,
    report: Mapping[str, Any],
) -> dict[str, Any]:
    """Expose, rather than silently hide, differences between live and final review."""
    completed_statuses = {"clear", "moderate", "critical"}
    live_findings: list[dict[str, Any]] = []
    if isinstance(live_audits, Mapping):
        for value in live_audits.values():
            if not isinstance(value, Mapping):
                continue
            if str(value.get("status", "")).strip().casefold() not in completed_statuses:
                continue
            live_findings.extend(
                dict(item)
                for item in (value.get("findings", []) or [])
                if isinstance(item, Mapping)
            )

    grouped = report.get("errors_by_section", {}) if isinstance(report, Mapping) else {}
    final_findings = [
        dict(item)
        for items in (grouped.values() if isinstance(grouped, Mapping) else [])
        for item in (items or [])
        if isinstance(item, Mapping)
    ]
    live_by_key = {_finding_identity(item): item for item in live_findings}
    final_by_key = {_finding_identity(item): item for item in final_findings}
    missing_from_final = [item for key, item in live_by_key.items() if key not in final_by_key]
    new_in_final = [item for key, item in final_by_key.items() if key not in live_by_key]
    return {
        "status": "different" if missing_from_final else "consistent",
        "missing_from_final": missing_from_final,
        "new_in_final": new_in_final,
        "live_finding_count": len(live_by_key),
        "final_finding_count": len(final_by_key),
    }


__all__ = [
    "SECTION_PROFILES",
    "LIVE_SECTION_RECORD_FIELDS",
    "audit_domains_for",
    "scope_live_record",
    "summarize_live_report",
    "compare_live_with_final",
]
