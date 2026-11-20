"""Write section audit findings from compact audit tasks using a model provider.

The provider remains injectable. This module validates and normalizes provider
output but does not own provider configuration or pipeline orchestration.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from difflib import SequenceMatcher
from time import perf_counter
from typing import Any

from .provider import ModelProvider, parse_json_value
from .rule_text import RULE_TEXT

_SYSTEM_MESSAGE = (
    "This is a prescription audit system. Review only the provided candidates "
    "for the specified section. Do not invent new errors. Confirm, reject, or "
    "refine candidates using only the provided evidence. A candidate's "
    "why_candidate is an unverified hypothesis, not an established fact; the "
    "raw evidence takes priority. Interpret Bengali and English clinical text "
    "directly, including mixed-language and transliterated wording; do not treat "
    "language alone as missing, unclear, or unsupported evidence. If a candidate is not "
    "supported, omit it. Return only one valid JSON object, with no markdown, "
    "code fence, explanation, or text before or after the object. Do not include "
    "<think>, reasoning, chain-of-thought, analysis, markdown, or prose. "
    "Output JSON only."
)


def _final_rule_name(value: Any) -> str:
    name = str(value or "").strip()
    return name[: -len("_candidate")] if name.endswith("_candidate") else name


def _group_candidates_for_prompt(candidates: list[Any]) -> list[Any]:
    """Group sibling candidates that share identical evidence text into one prompt entry.

    rule_candidate_engine.py deliberately raises several narrow candidates (different
    rule_id, same evidence) for one piece of text, e.g. a single chief-complaint line gets
    separate severity/site/demographic/temporal candidates. Sending that evidence once per
    candidate repeats the same sentence 4-5 times in the prompt. This groups them into
    {"evidence": ..., "checks": [...]} so the model reads the evidence once and judges each
    check against it. It is a prompt-rendering change only: every check keeps its own
    candidate_id/rule_id/rule_name untouched, and matching in _select_candidate /
    _normalize_findings runs against the ORIGINAL ungrouped task["candidates"], never
    against this grouped view, so confirm/reject/scoring behavior cannot be affected by it.
    """
    groups: dict[str, list[Mapping[str, Any]]] = {}
    order: list[str] = []
    for item in candidates:
        if not isinstance(item, Mapping):
            key = f"__non_mapping__\x00{id(item)}"  # unique key: never grouped
            order.append(key)
            groups[key] = [item]
            continue
        evidence = str(item.get("evidence", "") or "").strip()
        section = str(item.get("section", "") or "").strip()
        key = f"{section}\x00{evidence}" if evidence else f"__ungrouped__\x00{id(item)}"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(item)
    result: list[Any] = []
    for key in order:
        items = groups[key]
        if len(items) == 1 or not isinstance(items[0], Mapping):
            result.append(items[0])
            continue
        shared = {"section", "evidence"}
        checks = [{k: v for k, v in item.items() if k not in shared} for item in items]
        result.append({"section": items[0].get("section"), "evidence": items[0].get("evidence"), "checks": checks})
    return result


def _compact_task(task: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "prescription_id",
        "section",
        "fields_used",
        "clinical_evidence",
        "patient_context",
        "normalized_medicines",
        "medicine_facts",
        "followup_context",
        "instructions",
    )
    compact = {key: task[key] for key in keys if key in task}
    # CC has a detailed, section-specific contract in build_audit_prompt().
    # Repeating the same long contract inside TASK_JSON distracted smaller
    # instruction-following models from the actual per-row semantic decision
    # and needlessly increased live-check latency.
    if str(task.get("section", "") or "").strip() == "CC":
        compact.pop("instructions", None)
    compact["candidates"] = _group_candidates_for_prompt(
        [_compact_candidate(item) for item in task.get("candidates", []) or []]
    )
    return compact


def _compact_candidate(candidate: Any) -> Any:
    if not isinstance(candidate, Mapping):
        return candidate
    keep = (
        "candidate_id",
        "section",
        "rule_name",
        "rule_id",
        "why_candidate",
        "evidence",
        "generic_id",
        "generic_name",
        "dosage_form",
        "instruction_route",
        "instruction_text",
        "patient_risk_type",
        "patient_evidence",
        "medicine_fact_evidence",
        "source_field",
        "calculated_daily_dose_mg",
        "calculated_primary_daily_dose_mg",
        "primary_component_name",
        "strength_mg",
        "primary_strength_mg",
        "units_per_day",
        "administrations_per_day",
        "interval_pattern",
        "duration_days",
        "guideline_duration_min_days",
        "guideline_duration_max_days",
        "rag_max_daily_dose_mg",
        "rag_usual_daily_dose_mg",
        "rag_expected_administrations_per_day",
        "rag_expected_interval_pattern",
        "rag_per_dose_mg",
        "importance",
        "evidence_strength",
        "guideline_fact_id",
        "guideline_fact_evidence",
        "guideline_source_id",
        "guideline_source_page",
        "guideline_condition",
        "guideline_fact_type",
    )
    return {key: candidate[key] for key in keep if key in candidate}


def build_audit_prompt(task: Mapping[str, Any], repair_instruction: str = "") -> str:
    """Serialize one compact section task into the provider user prompt."""
    section = str(task.get("section", "")).strip()
    empty_output = json.dumps(
        {"section": section, "findings": []},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    skeleton = (
        "{\"section\":"
        + json.dumps(section, ensure_ascii=False)
        + ",\"findings\":[{\"candidate_id\":\"copy from candidate\",\"rule_name\":\"...\","
        "\"rule_id\":\"...\",\"why\":\"...\","
        "\"correction\":\"...\",\"evidence\":\"...\","
        "\"confidence\":\"low|medium|high\","
        "\"concern_level\":\"critical|major|moderate|minor\"}]}"
    )
    if section == "CC":
        # Keep the interactive free-text classifier compact.  The candidate
        # list remains comprehensive, but a single, ordered checklist is more
        # reliable for multilingual/Banglish input than repeating the same
        # policy in the prose prompt and again inside TASK_JSON.
        lines = [
            "Audit the chief-complaint candidates only; do not invent a new candidate.",
            "Interpret Bengali, English, mixed language, and nonstandard Romanized Bangla/Banglish literally. Do not invent an unstated symptom.",
            "Evaluate every candidate independently and return every supported finding.",
            'Some candidates appear grouped as {"evidence":...,"checks":[...]}: every entry in checks shares that one evidence text. Judge each entry in checks independently against it, exactly as you would an ungrouped candidate, and return a separate finding (with that entry\'s own candidate_id and rule_id) for each one that is supported. Omit any entry in checks that is not supported.',
            "MANDATORY FIRST DECISION FOR EACH CC.1: Does the literal text state an abnormal symptom, health problem, clinical concern, or valid encounter reason? If NO, include that CC.1 candidate in findings.",
            "A statement containing only a name/pronoun and an ordinary action such as eating, sleeping, walking, playing, or working is not a clinical complaint unless the text explicitly says unable, reduced, excessive, painful, difficult, or changed. Duration alone does not make it clinical.",
            "CC.term.unclear: flag only genuinely unrecognizable or clearly mistyped text; accept understandable colloquial and transliterated wording.",
            "CC.2: require severity, measured value, pattern, character, or functional effect only when it materially affects interpretation or triage.",
            "CC.3: omit when a usable duration/onset is present. Digits, number words, 'a few days', today, yesterday, and since morning count. 'facial pimple five days' HAVE duration and MUST NOT produce CC.3. Recent/recently/new/longstanding remain vague.",
            "CC.4: require site, source, distribution, or laterality only when clinically meaningful, including paired structures.",
            "CC.context.demo: flag genuine age, reproductive, sex/gender, or anatomical incompatibility; request verification when anatomy is uncertain.",
            "CC.time.plausibility: flag an atypical timeframe as needing clarification, not automatically impossible.",
            "CC.context.conflict and CC.hs: flag only a direct contradiction after considering timing, intermittence, treatment, and physiology.",
            "For each finding copy the exact candidate_id and rule_id. Keep why and correction to 25 words each. Use concern_level critical, major, moderate, or minor.",
            "Return JSON only with no markdown, analysis, or prose outside JSON.",
            f"REQUIRED_JSON_SKELETON={skeleton}",
            "If no candidate is supported, return exactly " + empty_output,
        ]
        if repair_instruction:
            lines.append(repair_instruction)
        lines.extend(["TASK_JSON=" + json.dumps(_compact_task(task), ensure_ascii=False, separators=(",", ":"))])
        return "\n".join(lines)

    lines = [
        "This is a prescription audit system.",
        f"Review only the provided candidates for section {section}.",
        "Do not invent new errors.",
        "Confirm, reject, or refine candidates using the provided evidence.",
        "When a candidate_id is supplied, copy that exact candidate_id into its finding.",
        "Omit every candidate that is not supported.",
        'Some candidates appear grouped as {"evidence":...,"checks":[...]}: every entry in checks shares that one evidence text. Judge each entry in checks independently against it, exactly as you would an ungrouped candidate, and return a separate finding (with that entry\'s own candidate_id and rule_id) for each one that is supported. Omit any entry in checks that is not supported.',
        "Never return a finding merely to state that the current item is "
        "appropriate, clinically relevant, or requires no action; reject that "
        "candidate by omitting it.",
        "Be concise: why <= 25 words, correction <= 25 words.",
        "Do not debate inside JSON fields. Do not write phrases like 'wait', "
        "'let us re-evaluate', 'strictly speaking', or internal calculations. "
        "Use only the final conclusion.",
    ]
    if section == "CC":
        lines.extend(
            [
                "CC REVIEW ORDER: For each entered complaint, evaluate CC.1 first, then evaluate every other candidate independently. Return every supported finding.",
                "Interpret Bengali, English, mixed language, and nonstandard Romanized Bangla/Banglish literally before making any clinical inference.",
                "CC.1 IS MANDATORY: decide whether the literal text itself states an abnormal symptom, health problem, clinical concern, or valid encounter reason. If it states only a normal everyday action, confirm CC.1. Never convert an ordinary action into an unstated appetite, dietary, gastrointestinal, or behavioral complaint.",
                "A name or pronoun plus eating, sleeping, walking, playing, working, or another ordinary action is non-clinical unless the text explicitly says it is absent, reduced, excessive, painful, difficult, or changed. A duration does not make an ordinary action clinical.",
                "For CC.context.demo, confirm only a genuine demographic, anatomical, reproductive, or age-context incompatibility. Administrative gender alone does not always prove anatomy; when uncertain, request verification and do not call the complaint impossible.",
                "For CC.2, require characterization only when intensity, measured value, grade, pattern, character, or functional impact materially affects interpretation or triage. A current measured temperature may characterize fever; duration alone does not describe its degree or pattern.",
                "For CC.4, require site, source, distribution, or laterality only when clinically meaningful. For a paired structure, right, left, or bilateral wording may be needed; do not demand laterality for diffuse symptoms.",
                "For CC.time.plausibility, flag an atypical timeframe as needing clarification, not as medically impossible. Consider acute fluid shifts, measurement error, intermittent symptoms, and ambiguous wording.",
                "For CC.context.conflict, confirm only a direct contradiction after considering chronology and physiology. Amenorrhea and pregnancy are compatible, not contradictory.",
                "CC.3 DURATION CHECK: Read the candidate evidence itself before "
                "deciding. Omit CC.3 whenever the evidence contains any duration "
                "or onset expression.",
                "A duration remains documented when the number is written as a "
                "word and when 'for' or 'since' is omitted. Therefore 'facial "
                "pimple five days', 'rash for five days', and 'cough 5 days' all "
                "HAVE duration and MUST NOT produce CC.3.",
                "Approximate bounded expressions such as 'a few days', 'today', "
                "'yesterday', and 'since morning' count as duration/onset. Vague "
                "words such as 'recent', 'recently', 'new', 'longstanding', or "
                "'for some time' do not provide a usable timeframe and should "
                "be confirmed as a clarification concern.",
            ]
        )
    if section == "Hx":
        lines.extend(
            [
                "PAST-HISTORY REVIEW: Interpret every entry in its original language and compare it with demographics, complaints, obstetric context, screening, examination, and actual investigation results supplied in the task.",
                "Evaluate every history candidate independently and return every separately supported concern.",
                "Past history is not automatically a current diagnosis. A normal present examination does not by itself disprove a chronic, intermittent, treated, or resolved condition.",
                "Request status, stage, severity, site, laterality, procedure, allergen, reaction, or onset only when the missing detail could materially change present care.",
                "Treat unusual but possible histories as needing clarification, not as impossible. Confirm a conflict only when the supplied facts directly contradict after chronology and disease state are considered.",
                "Do not infer an allergy, organ impairment, operation, or disease that is not stated in the source history.",
                "Each history candidate is a check to perform, not a suspected error. Decide it with your own medical knowledge and confirm it only when that check actually finds a problem.",
                "Hx.term.unclear: confirm when a condition name is unrecognizable, not a real disease, or clearly mistyped. Accept understandable Bengali, Banglish, and shorthand such as DM, HTN, BA, IHD, sugar, pressure, or gastric.",
                "Hx.context.demo: confirm when the history cannot fit the patient's age or sex/anatomy, such as a caesarean section, hysterectomy, or past pregnancy for a male, prostate surgery for a female, or menopause in a child.",
                "Hx.detail: confirm when a missing detail would change present care, such as an allergy without the allergen or reaction, an operation without the procedure, or 'heart disease' or 'kidney problem' with no type or status.",
                "Hx.time.plausibility: confirm when the timing is impossible or unclear for this patient, such as a duration longer than the patient's age, an onset in the future, or a lifelong adult disease in an infant.",
                "Hx.context.conflict: confirm when the history directly contradicts current complaints, examination, results, or obstetric notes, such as 'never pregnant' with antenatal notes, or 'no diabetes' with diabetic results.",
                "Hx.duplicate: confirm when two entries are the same condition under different names (high pressure and hypertension, sugar and diabetes) or when one condition is stated as both present and absent.",
                "For each confirmed check, why must say in your own words what is clinically wrong with this specific history, not repeat the check description.",
            ]
        )
    if repair_instruction:
        lines.append(repair_instruction)
    lines.extend(
        [
            "Every finding must come from exactly one provided candidate only.",
            "For each finding include concern_level. Use critical for immediate "
            "or high-risk safety issues; major for important clinician/"
            "pharmacist review; moderate for relevant non-urgent review; minor "
            "for documentation/completeness issues.",
            "Write corrections in audit/review language, not as direct treatment orders.",
            "Avoid direct imperatives such as: discontinue immediately, stop the "
            "medicine, prescribe a specific replacement, or switch to a named "
            "alternative.",
        ]
    )
    if section == "Rx":
        lines.extend(
            [
                "For Rx.route.mismatch, confirm a finding when the supplied "
                "dosage_form and instruction_route are explicitly different "
                "routes (for example, an oral tablet with eye or ear use); do "
                "not reinterpret the route from general clinical knowledge.",
                "Confirm an Rx duration mismatch only when the supplied evidence "
                "explicitly states a duration and is applicable to the documented "
                "medicine, diagnosis, and patient context; otherwise omit it.",
            ]
        )
    if section in {"Rx", "Adv"}:
        lines.extend(
            [
                "Make each correction match the candidate risk type. For renal "
                "risk, mention renal function, eGFR/CrCl, and dose-adjustment "
                "review. For pregnancy risk, mention pregnancy contraindication/"
                "risk and guideline-supported alternatives. For allergy or "
                "hypersensitivity risk, mention verifying allergy history and "
                "avoiding the same drug/class if confirmed. For hepatic risk, "
                "mention hepatic impairment, liver function, contraindication, "
                "or monitoring review. For unknown safety risks, use general "
                "audit-review language.",
                "Use urgent clinician/pharmacist review language only for contraindicated or high-risk findings.",
            ]
        )
    if section == "Adv":
        lines.append(
            "For Adv.preg.danger, prefer urgent clinician/OB assessment or "
            "referral documentation plus danger-sign counseling; do not claim "
            "fetal distress, shock, or hemodynamic instability unless the "
            "evidence explicitly documents it."
        )
    lines.extend(
        [
            "Do not return diagnosis, explanation, markdown, bullets, or prose outside JSON.",
            "Do not include <think>, reasoning, chain-of-thought, analysis, markdown, or prose. Output JSON only.",
            f"REQUIRED_JSON_SKELETON={skeleton}",
            "If no candidate is a true error, return exactly " + empty_output,
            "Return only one valid JSON object and nothing else.",
        ]
    )
    lines.append("TASK_JSON=" + json.dumps(_compact_task(task), ensure_ascii=False, separators=(",", ":")))
    return "\n".join(lines)


def _section_value(value: Mapping[str, Any], section: str) -> Any:
    if section in value:
        return value[section]
    target = section.casefold()
    for key, item in value.items():
        if str(key).strip().casefold() == target:
            return item
    return None


def _canonical_payload(value: Any, section: str, depth: int = 0) -> Mapping[str, Any]:
    empty = {"section": section, "findings": []}
    if depth > 4:
        return empty
    if isinstance(value, list):
        return {"section": section, "findings": value}
    if not isinstance(value, Mapping):
        return empty
    if not value:
        return empty

    # detected_errors: a fine-tune asked with a chat prompt may still answer in its training format.
    for key in ("findings", "errors", "audit_findings", "detected_errors"):
        if key in value:
            findings = value[key]
            return {"section": section, "findings": findings} if isinstance(findings, list) else empty

    finding_keys = {
        "rule_name",
        "rule_id",
        "why",
        "why_candidate",
        "correction",
        "evidence",
    }
    if finding_keys.intersection(value):
        return {"section": section, "findings": [dict(value)]}

    if "result" in value:
        return _canonical_payload(value["result"], section, depth + 1)

    section_outputs = value.get("section_outputs")
    if isinstance(section_outputs, Mapping):
        selected = _section_value(section_outputs, section)
        if selected is not None:
            return _canonical_payload(selected, section, depth + 1)

    selected = _section_value(value, section)
    if selected is not None:
        return _canonical_payload(selected, section, depth + 1)

    return empty


def _parse_response(response: Any, section: str) -> Mapping[str, Any]:
    payload = parse_json_value(response) if isinstance(response, str) else response
    return _canonical_payload(payload, section)


def _confidence(value: Any) -> str:
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"low", "medium", "high"}:
            return normalized
        try:
            value = float(normalized)
        except ValueError:
            return "medium"
    if isinstance(value, (int, float)):
        if float(value) >= 0.8:
            return "high"
        if float(value) >= 0.5:
            return "medium"
        return "low"
    return "medium"


def _concern_level(value: Any, candidate: Mapping[str, Any]) -> str:
    text = str(value or "").strip().casefold().replace("_", " ")
    rule_id = str(candidate.get("rule_id", "") or "").casefold()
    # Fixed severity for bounded live-audit rules.
    if rule_id == "dx.vital.extreme":
        return "critical"
    # A missing/unclear documented indication is a review/documentation issue,
    # not proof that the medicine is clinically inappropriate. Explicit
    # contraindication or guideline-avoid evidence is represented by separate
    # safety rules and may still be major or critical.
    if rule_id == "rx.indication.missing":
        return "moderate"
    if rule_id == "adv.content.unrecognized":
        return "minor"
    if rule_id == "inv.term.unrecognized":
        return "minor"
    if rule_id in {"cc.1", "cc.term.unclear", "dx.term.unclear", "rx.term.unclear"}:
        return "minor"
    if text in {"critical", "major", "moderate", "minor"}:
        return text
    importance = str(candidate.get("importance", "") or "").casefold()
    evidence_strength = str(candidate.get("evidence_strength", "") or "").casefold()
    rule_name = str(candidate.get("rule_name", "") or "").casefold()
    combined = f"{rule_id} {rule_name}"
    if importance in {"critical_review", "critical", "high"}:
        return "critical"
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
    if any(marker in combined for marker in ("dose.high", "dose.weight", "strength.mismatch", "rag.avoid")):
        return "major"
    # Display level only (the model decided the finding): something impossible for the patient's sex or age
    # (pregnancy in a man, hysterectomy in a man) is major in every section; complaint rules defaulted to "minor".
    if rule_id.endswith(".demo") or ".context.demo" in rule_id or "demographic" in rule_name:
        return "major"
    if importance in {"moderate_review", "moderate"} or evidence_strength == "medium":
        return "moderate"
    if rule_id.startswith("cc.") or importance in {"minor_documentation", "minor"}:
        return "minor"
    return "moderate"


def _candidate_indexes(
    task: Mapping[str, Any],
) -> tuple[
    dict[str, Mapping[str, Any]],
    dict[str, list[Mapping[str, Any]]],
    dict[str, list[Mapping[str, Any]]],
]:
    by_candidate_id: dict[str, Mapping[str, Any]] = {}
    by_id: dict[str, list[Mapping[str, Any]]] = {}
    by_name: dict[str, list[Mapping[str, Any]]] = {}
    for candidate in task.get("candidates", []):
        if not isinstance(candidate, Mapping):
            continue
        candidate_id = str(candidate.get("candidate_id", "")).strip()
        rule_id = str(candidate.get("rule_id", "")).strip()
        rule_name = _final_rule_name(candidate.get("rule_name", ""))
        if candidate_id:
            by_candidate_id[candidate_id] = candidate
        if rule_id:
            by_id.setdefault(rule_id, []).append(candidate)
        if rule_name:
            by_name.setdefault(rule_name, []).append(candidate)
    return by_candidate_id, by_id, by_name


def _candidate_match_score(candidate: Mapping[str, Any], raw: Mapping[str, Any]) -> int:
    """Prefer the same per-item candidate when one rule has several items."""
    candidate_evidence = str(candidate.get("evidence", "") or "").casefold()
    returned = " ".join(str(raw.get(key, "") or "") for key in ("evidence", "why", "correction")).casefold()
    if not candidate_evidence or not returned:
        return 0
    if candidate_evidence in returned or returned in candidate_evidence:
        return 10000 + min(len(candidate_evidence), len(returned))
    candidate_tokens = set(re.findall(r"[\w/+.-]+", candidate_evidence))
    returned_tokens = set(re.findall(r"[\w/+.-]+", returned))
    return len(candidate_tokens.intersection(returned_tokens))


def _select_candidate(
    raw: Mapping[str, Any],
    returned_id: str,
    returned_name: str,
    by_candidate_id: Mapping[str, Mapping[str, Any]],
    by_id: Mapping[str, Sequence[Mapping[str, Any]]],
    by_name: Mapping[str, Sequence[Mapping[str, Any]]],
) -> Mapping[str, Any] | None:
    candidate_id = str(raw.get("candidate_id", "") or "").strip()
    if candidate_id and candidate_id in by_candidate_id:
        return by_candidate_id[candidate_id]
    options = list(by_id.get(returned_id, ())) if returned_id else []
    if not options and returned_name:
        options = list(by_name.get(returned_name, ()))
    if not options:
        return None
    if len(options) == 1:
        return options[0]
    return max(options, key=lambda item: _candidate_match_score(item, raw))


_DIRECT_CORRECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bdiscontinue\b(?:\s+\w+){0,3}\s+\bimmediately\b", re.I),
    re.compile(
        r"\bstop\s+(?:the\s+)?(?:medicine|medication|drug|[A-Z][\w+-]*)\b",
        re.I,
    ),
    re.compile(
        r"\bdiscontinue\s+(?:the\s+)?(?:medicine|medication|drug|[A-Z][\w+-]*)\b",
        re.I,
    ),
    re.compile(r"\bswitch\s+to\s+[^.]+", re.I),
    re.compile(r"\bprescribe\s+[^.]+", re.I),
)


_RENAL_CONTEXT_PATTERN = re.compile(
    r"\b(?:renal|kidney|egfr|gfr|crcl|creatinine clearance|dose adjustment)\b",
    re.I,
)
_INTERNAL_DEBATE_PATTERN = re.compile(
    r"\b(?:wait|let'?s\s+re-?evaluate|strictly speaking|look closer|"
    r"technically within|however, often|the candidate says)\b",
    re.I,
)


def _correction_risk_type(rule_name: str, rule_id: str) -> str:
    key = f"{rule_name} {rule_id}".casefold()
    if "adv.preg.danger" in key or "pregnancy_danger" in key:
        return "pregnancy_danger"
    if "renal" in key or "kidney" in key:
        return "renal"
    if "pregnan" in key:
        return "pregnancy"
    if "lactat" in key or "breastfeed" in key:
        return "lactation"
    if "allerg" in key or "hypersens" in key:
        return "allergy"
    if "hepatic" in key or "liver" in key:
        return "hepatic"
    return "general"


def _risk_review_correction(risk_type: str) -> str:
    if risk_type == "pregnancy_danger":
        return (
            "Review and document urgent clinician/OB assessment or referral, "
            "follow-up plan, and pregnancy danger-sign counseling based on the "
            "documented danger-sign evidence."
        )
    if risk_type == "renal":
        return (
            "Review the prescription against current renal function, including "
            "eGFR/CrCl, and verify whether dose adjustment or an alternative is "
            "needed with clinician/pharmacist review."
        )
    if risk_type == "pregnancy":
        return (
            "Review pregnancy contraindication or risk before continuing; urgent "
            "clinician/pharmacist review is recommended, and any pregnancy-safe "
            "alternative should follow local guideline and clinician judgement."
        )
    if risk_type == "lactation":
        return (
            "Review lactation safety before continuing; clinician/pharmacist "
            "review is recommended, and any breastfeeding-compatible alternative "
            "should follow local guideline and clinician judgement."
        )
    if risk_type == "allergy":
        return (
            "Verify the allergy or hypersensitivity history and review whether "
            "the same drug or related class should be avoided if confirmed, with "
            "clinician/pharmacist review."
        )
    if risk_type == "hepatic":
        return (
            "Review hepatic impairment or liver function risk, including any "
            "contraindication or monitoring requirement, with clinician/"
            "pharmacist review."
        )
    return (
        "Review the prescription and supporting evidence with clinician/"
        "pharmacist review, especially if contraindicated or high risk."
    )


def _correction_needs_context_replacement(text: str, risk_type: str) -> bool:
    if any(pattern.search(text) for pattern in _DIRECT_CORRECTION_PATTERNS):
        return True
    if risk_type != "renal" and _RENAL_CONTEXT_PATTERN.search(text):
        return True
    return False


def _sanitize_correction(text: str, rule_name: str = "", rule_id: str = "") -> str:
    """Soften provider corrections and keep them matched to the candidate risk."""
    risk_type = _correction_risk_type(rule_name, rule_id)
    cleaned = text.strip()
    if not cleaned or _correction_needs_context_replacement(cleaned, risk_type):
        cleaned = _risk_review_correction(risk_type)
    if risk_type == "pregnancy_danger":
        cleaned = re.sub(
            r"(?i)\bhemodynamic(?:ally)?\s+instability\b|\bhemodynamic\s+instability\b",
            "abnormal vital signs",
            cleaned,
        )
        cleaned = re.sub(r"(?i)\bfetal\s+distress\b", "reduced fetal movement concern", cleaned)
        cleaned = re.sub(r"(?i)\bshock\b", "high-risk clinical concern", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if cleaned and cleaned[-1] not in ".!?":
        cleaned += "."
    return cleaned


def _sanitize_why(text: str, candidate: Mapping[str, Any]) -> str:
    cleaned = re.sub(r"\s+", " ", text.strip())
    fallback = str(candidate.get("why_candidate", "") or "").strip()
    if _INTERNAL_DEBATE_PATTERN.search(cleaned):
        cleaned = fallback or "Provided evidence supports this audit finding."
    if len(cleaned) > 450:
        cleaned = cleaned[:447].rstrip() + "..."
    return cleaned


def _provider_rejected_candidate(raw: Mapping[str, Any], why: str, correction: str) -> bool:
    # Only explicit decision fields can reject on a bare keyword; free text such as
    # "CBC omitted from the work-up" is a valid finding and must not be discarded.
    decision = " ".join(
        "" if raw.get(key) is None else str(raw.get(key)) for key in ("status", "decision", "supported")
    ).casefold()
    if re.search(r"\b(reject(?:ed)?|omit(?:ted)?|not\s+supported|false|no)\b", decision):
        return True
    combined = f"{why.casefold()} {correction.casefold()} {str(raw.get('comment', '') or '').casefold()}"
    if re.search(r"\b(candidate\s+(?:is\s+)?rejected|not\s+supported|no\s+true\s+error|not\s+an\s+error)\b", combined):
        return True
    if re.search(
        r"\b("
        r"no\s+correction\s+(?:is\s+)?required|"
        r"no\s+correction\s+needed|"
        r"no\s+(?:further\s+)?action\s+(?:is\s+)?required|"
        r"no\s+(?:further\s+)?action\s+needed|"
        r"no\s+(?:further\s+)?changes?\s+(?:is\s+|are\s+)?(?:needed|required)|"
        r"omit\s+(?:this\s+)?candidate"
        r")\b",
        combined,
    ):
        return True
    # Statements that explicitly say nothing is wrong ("No clear violation", "without contradicting").
    if re.search(
        r"\bno\s+(?:clear\s+|significant\s+|apparent\s+|obvious\s+|further\s+)?"
        r"(?:violations?|issues?|concerns?|errors?|gaps?|problems?|mismatch(?:es)?|contraindications?|discrepanc(?:y|ies))\b"
        r"|\bwithout\s+(?:any\s+)?(?:contradict|conflict)"
        r"|\b(?:does|do|did)\s+not\s+(?:contradict|violate|conflict)\b"
        r"|\bnothing\s+(?:is\s+)?(?:wrong|missing|to\s+(?:flag|report|note|add))\b"
        r"|\bno\s+concerns?\s+(?:noted|identified|found)\b",
        combined,
    ):
        return True
    if re.search(
        r"\b(?:is|are)\s+(?:being\s+)?(?:addressed|met|satisfied|justified|treated|covered|documented|supported)\b"
        r"|\b(?:addresses|justifies|supports|explains)\s+the\b"
        r"|\bis\s+a\s+(?:standard|recognized|recognised|valid|common|well-known|routine)\b"
        r"|\b(?:single|only|one)\s+diagnosis\b",
        combined,
    ) and not re.search(
        # "no" added: a model phrasing a real gap as "No site is documented" (negator before the
        # subject, not before the verb) was wrongly matching "is documented" above and getting
        # rejected as if the candidate were confirmed-fine. The sibling check just below this one
        # already includes "no" in its own exclusion list; this one was missing it.
        r"\b(?:not|no|never|without|insufficient|lacks?|missing|inadequately|partially|unaddressed|unsupported)\b",
        combined,
    ):
        return True
    # A finding whose own explanation says the item is fine is a rejection in disguise
    # (weaker models often "confirm" a candidate while describing it as relevant/appropriate).
    if re.search(
        # (?:\w+ly\s+)? allows any adverb before the adjective (clinically, physiologically,
        # generally, ...), not just the two that were hardcoded -- "is physiologically plausible"
        # was slipping through before because "physiologically" wasn't "clinically" or "directly".
        r"\b(?:is|are|was|were|seems|appears)\s+(?:\w+ly\s+)?"
        r"(?:relevant|indicated|appropriate|justified|reasonable|acceptable|adequate|consistent|correct|valid|"
        r"plausible|typical|expected|unremarkable)\b",
        combined,
    ) and not re.search(
        r"\b(?:not|no|never|unrelated|irrelevant|lacks?|missing|without|inappropriate|incorrect|invalid|"
        r"inconsistent|unjustified|insufficient|mismatch)\b",
        combined,
    ):
        return True
    if re.search(
        r"\b(?:not\s+clinically\s+significant|clinically\s+insignificant|"
        r"not\s+a\s+(?:true\s+)?concern|non[-\s]?issue)\b",
        combined,
    ) and not re.search(
        r"\b(?:no|missing|omits|lacks|contraindicat|avoid|exceeds|mismatch|inappropriate|unsafe|"
        r"not\s+recommended|requires\s+review|risk|interaction|duplicate|overlap)\b",
        combined,
    ):
        return True
    supportive = re.search(
        r"\b("
        r"is\s+consistent\s+with|"
        r"appears\s+consistent\s+with|"
        r"is\s+appropriate|"
        r"appears\s+appropriate|"
        r"appropriately\s+(?:reflects|documents|addresses)|"
        r"aligns\s+with|"
        r"is\s+in\s+line\s+with|"
        r"adequately\s+(?:reflects|documents|addresses)|"
        r"no\s+(?:gap|issue|error)\s+(?:is\s+)?identified"
        r")\b",
        combined,
    )
    review_only = re.search(
        r"\b(confirm|continue|ensure documentation aligns|monitor|follow\s*up)\b",
        combined,
    )
    if (
        supportive
        and review_only
        and not re.search(
            r"\b(no|missing|omits|lacks|contraindicat|avoid|exceeds|mismatch|inappropriate|unsafe|not\s+recommended|requires\s+review|risk)\b",
            combined,
        )
    ):
        return True
    return False


def _is_echo(text: str, reference: str) -> bool:
    """True when `text` is (nearly) the reference sentence repeated back."""
    a = " ".join(re.findall(r"\w+", text.casefold()))
    b = " ".join(re.findall(r"\w+", reference.casefold()))
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return len(a) >= 0.7 * len(b) or len(b) >= 0.7 * len(a)
    return SequenceMatcher(None, a, b).ratio() >= 0.8


# Rule IDs that different candidate generators raise from a different angle, but that in
# practice confirm together for the SAME underlying fact once it's genuinely true -- e.g.
# CC.context.demo ("does this complaint fit the patient's demographics") and
# CC.context.conflict ("does this complaint contradict other context") both legitimately fire,
# worded differently, for one real conflict such as a male patient with a pregnancy complaint.
# This is a short, reviewed list of rule_id pairs actually observed double-firing -- not a
# generic text-similarity guess -- so extending it means adding a pair you've confirmed
# overlaps, not changing behavior for everything. When 2+ of a group are confirmed for the
# same section/task, only the single most severe one is kept; the rest are dropped as the
# same concern restated.
_OVERLAPPING_RULE_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"CC.context.demo", "CC.context.conflict"}),
    frozenset({"Dx.cc.1", "Dx.cc.2"}),
    frozenset({"Dx.hs.1", "Dx.hs.2"}),
    # Observed double-firing together for an investigation that doesn't fit the patient (e.g. a
    # pregnancy ultrasound for a 49-year-old male): Inv.demo.1 asks "is this age/gender
    # appropriate", Inv.relevance asks "is this clinically relevant to the patient" -- same
    # underlying mismatch from two angles.
    frozenset({"Inv.demo.1", "Inv.relevance"}),
    # Taught together (v7 data, 2026-11-14) when the drug facts list the patient's condition as a
    # contraindication (e.g. propranolol in asthma): one drug-disease conflict, two rule names.
    frozenset({"Rx.contra", "Rx.cc.mis"}),
)
_CONCERN_RANK = {"minor": 1, "moderate": 2, "major": 3, "critical": 4}


def _consolidate_overlapping_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only the most severe finding from each overlapping rule-id group (see above)."""

    def group_for(rule_id: str) -> frozenset[str] | None:
        for group in _OVERLAPPING_RULE_GROUPS:
            if rule_id in group:
                return group
        return None

    kept_by_group: dict[frozenset[str], dict[str, Any]] = {}
    result: list[dict[str, Any]] = []
    for item in findings:
        rule_id = str(item.get("rule_id", "")).strip()
        group = group_for(rule_id)
        if group is None:
            result.append(item)
            continue
        current = kept_by_group.get(group)
        if current is None:
            kept_by_group[group] = item
            result.append(item)
            continue
        new_rank = _CONCERN_RANK.get(str(item.get("concern_level", "")).casefold(), 0)
        current_rank = _CONCERN_RANK.get(str(current.get("concern_level", "")).casefold(), 0)
        if new_rank > current_rank:
            result[result.index(current)] = item
            kept_by_group[group] = item
        # else: new item is the same or a weaker restatement of the one already kept -- drop it.
    return result


def _normalize_findings(
    task: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> list[dict[str, Any]]:
    section = str(task.get("section", "")).strip()
    by_candidate_id, by_id, by_name = _candidate_indexes(task)
    findings: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in payload["findings"]:
        if not isinstance(raw, Mapping):
            continue
        returned_id = str(raw.get("rule_id", "")).strip()
        returned_name = _final_rule_name(raw.get("rule_name", ""))
        candidate = _select_candidate(
            raw,
            returned_id,
            returned_name,
            by_candidate_id,
            by_id,
            by_name,
        )
        if candidate is None:
            if not raw.get("model_first") or not returned_id:
                continue
            # The model found this on its own (no rule-engine check proposed it): its finding stands as a
            # candidate of its own. No hypothesis text, so the "only repeats the hypothesis" filter below
            # does not apply.
            candidate = {
                "rule_id": returned_id,
                "rule_name": raw.get("rule_name", ""),
                "section": section,
                "candidate_id": raw.get("candidate_id", ""),
                "evidence": raw.get("evidence", ""),
                "why_candidate": "",
                "evidence_strength": "model",
            }
        rule_id = str(candidate.get("rule_id", returned_id)).strip()
        rule_name = _final_rule_name(candidate.get("rule_name", returned_name))
        why = str(raw.get("why") or raw.get("why_candidate") or candidate.get("why_candidate") or "").strip()
        # A model that only repeats the candidate's own hypothesis ("Review whether ...") has not found
        # anything. Weak models do this for every candidate, which floods the panel with identical
        # advice. Evidence-backed (high) candidates and the rules whose wording is fixed below are exempt.
        hypothesis = str(candidate.get("why_candidate", "") or "").strip()
        own_why = str(raw.get("why") or "").strip()
        candidate_evidence = str(raw.get("evidence") or candidate.get("evidence") or "").strip()
        if (
            hypothesis
            and str(candidate.get("evidence_strength", "")).casefold() != "high"
            and rule_id not in {"CC.1", "Adv.missing"}
            and (
                not own_why
                or _is_echo(own_why, hypothesis)
                or _is_echo(own_why, candidate_evidence)  # "Past history: Diabetic" explains nothing
                or len(re.findall(r"\w+", own_why)) < 5  # too short to be a reason
            )
        ):
            continue
        why = _sanitize_why(why, candidate)
        correction = str(
            raw.get("correction")
            or raw.get("suggested_correction")
            or candidate.get("correction")
            or candidate.get("suggested_correction")
            or candidate.get("recommendation")
            or "Review and correct according to protocol."
        ).strip()
        if (hypothesis and _is_echo(correction, hypothesis)) or _is_echo(correction, candidate_evidence):
            correction = "Review and correct according to protocol."  # never show the hypothesis/evidence as the fix
        # "Did you mean ...?" only makes sense for spelling/term rules; weak models copy it everywhere.
        is_term_rule = any(word in rule_id.casefold() for word in ("term", "unrecognized", "unclear"))
        if (
            re.match(r"(?i)\s*did you mean", correction) and not is_term_rule
        ) or correction == "Review and correct according to protocol.":
            correction = RULE_TEXT.get(rule_id, ("", correction))[1] or correction
        if _provider_rejected_candidate(raw, why, correction):
            continue
        # The model decides whether CC.1 is supported.  Once confirmed, use
        # neutral wording so an uncertain transliteration is not displayed as
        # an invented diagnosis or an unreliable word-for-word translation.
        if rule_id == "CC.1":
            why = "This entry does not clearly describe a clinical symptom, health concern, or reason for consultation."
            correction = "Clarify and document the patient's actual symptom or reason for consultation."
        # PrescribeX has a separate Follow-up writer section. Once the model has
        # confirmed Adv.missing, keep the displayed finding scoped to patient
        # advice rather than implying that follow-up belongs in Advice.
        if rule_id == "Adv.missing":
            why = (
                "Diagnosis or treatment is documented, but patient advice, "
                "counselling, precautions, or safety-net instructions are not recorded."
            )
            correction = (
                "Document relevant patient advice, precautions, and safety-net "
                "instructions for the prescribed treatment."
            )
        correction = _sanitize_correction(correction, rule_name, rule_id)
        evidence = str(raw.get("evidence", "")).strip() or str(candidate.get("evidence", "")).strip()
        if not rule_name or not rule_id or not why or not correction or not evidence:
            continue
        key = rule_name, rule_id, evidence
        if key in seen:
            continue
        # The same explanation for the same item under several rules is one concern, not several.
        text_key = (
            "text",
            " ".join(re.findall(r"\w+", evidence.casefold())),
            " ".join(re.findall(r"\w+", why.casefold())),
        )
        if text_key in seen:
            continue
        seen.add(key)
        seen.add(text_key)
        findings.append(
            {
                "candidate_id": str(candidate.get("candidate_id", "") or "").strip(),
                "section": section,
                "rule_name": rule_name,
                "rule_id": rule_id,
                "why": why,
                "correction": correction,
                "evidence": evidence,
                "confidence": _confidence(raw.get("confidence")),
                "concern_level": _concern_level(raw.get("concern_level"), candidate),
            }
        )
    return _consolidate_overlapping_findings(findings)


def _provider_model_name(provider: ModelProvider) -> str:
    return str(getattr(provider, "model", "") or getattr(provider, "deployment", "") or type(provider).__name__)


def _response_character_count(response: Any, provider: ModelProvider) -> int:
    provider_count = getattr(provider, "last_output_character_count", None)
    if isinstance(provider_count, int) and provider_count >= 0:
        return provider_count
    if isinstance(response, str):
        return len(response)
    try:
        return len(json.dumps(response, ensure_ascii=False, separators=(",", ":")))
    except (TypeError, ValueError):
        return 0


def _redact_credentials(text: str) -> str:
    redacted = text
    for name in (
        "GROQ_API_KEY",
        "OPENAI_API_KEY",
        "AZURE_OPENAI_API_KEY",
    ):
        secret = os.getenv(name, "")
        if secret:
            redacted = redacted.replace(secret, "[REDACTED]")
    return redacted


def _failed_response_preview(response: Any, provider: ModelProvider) -> str:
    if isinstance(response, str):
        text = response
    elif response is not None:
        try:
            text = json.dumps(response, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            text = str(response)
    else:
        text = str(getattr(provider, "last_response_text_preview", "") or "")
    return _redact_credentials(text)[:1000]


def _safe_parse_error(error: Exception) -> str:
    return _redact_credentials(f"{type(error).__name__}: {error}")[:1000]


def _is_non_retryable_provider_error(error: Exception) -> bool:
    """Return True when another identical provider call cannot succeed.

    Format/JSON failures still receive the normal repair attempts.  Provider
    authentication, permission, and invalid-request failures do not: retrying
    them only makes a live section check appear to hang.
    """
    status_code = getattr(error, "status_code", None)
    if status_code in {400, 401, 403, 404}:
        return True
    name = type(error).__name__.casefold()
    return name in {
        "authenticationerror",
        "permissiondeniederror",
        "badrequesterror",
        "notfounderror",
    }


def write_audit_sections(
    tasks: Sequence[Mapping[str, Any]],
    provider: ModelProvider,
    max_retries: int = 2,
    *,
    max_tokens: int = 1000,
) -> dict[str, Any]:
    """Call the provider per task and preserve successful partial outputs."""
    if max_retries < 0:
        raise ValueError("max_retries cannot be negative")
    section_outputs: dict[str, list[dict[str, Any]]] = {}
    warnings: list[str] = []
    model_calls: list[dict[str, Any]] = []
    model_name = _provider_model_name(provider)

    for task in tasks:
        section = str(task.get("section", "")).strip()
        if not section:
            warnings.append("audit task without a section was skipped")
            continue
        section_outputs.setdefault(section, [])
        repair = ""
        last_error: Exception | None = None
        attempts_used = 0
        for attempt in range(max_retries + 1):
            attempts_used = attempt + 1
            prompt = build_audit_prompt(task, repair)
            response: Any = None
            call_started = perf_counter()
            success = False
            error_type = ""
            parse_error = ""
            try:
                response = provider.audit(
                    system=_SYSTEM_MESSAGE,
                    prompt=prompt,
                    # ~140 tokens per finding: a fixed small budget truncated the JSON when a
                    # section had many candidates, and the whole section result was then lost.
                    max_tokens=min(max(max_tokens, 140 * len(task.get("candidates", []) or []) + 120), 3000),
                )
                payload = _parse_response(response, section)
                section_outputs[section].extend(_normalize_findings(task, payload))
                last_error = None
                success = True
            except Exception as exc:
                last_error = exc
                error_type = type(exc).__name__
                parse_error = _safe_parse_error(exc)
                repair = (
                    "REPAIR: The previous response was invalid or unavailable. "
                    "Return exactly one valid JSON object matching the required "
                    "shape, with no markdown, code fence, or explanation."
                )
            finally:
                model_calls.append(
                    {
                        "section": section,
                        "model": model_name,
                        "attempt": attempt + 1,
                        "duration_ms": round((perf_counter() - call_started) * 1000, 3),
                        "prompt_char_count": len(prompt),
                        "input_character_count": len(_SYSTEM_MESSAGE) + len(prompt),
                        "output_char_count": _response_character_count(response, provider),
                        "candidate_count": len(task.get("candidates", []) or []),
                        "success": success,
                        "error_type": error_type,
                        "response_text_preview": ("" if success else _failed_response_preview(response, provider)),
                        "parse_error": parse_error,
                    }
                )
            if (
                success
                or (last_error is not None and _is_non_retryable_provider_error(last_error))
                or attempt >= max_retries
            ):
                break
        if last_error is not None:
            warnings.append(
                f"section {section} provider failed after {attempts_used} attempt(s): {_safe_parse_error(last_error)}"
            )

    return {
        "section_outputs": section_outputs,
        "warnings": warnings,
        "model_calls": model_calls,
    }


__all__ = ["build_audit_prompt", "write_audit_sections"]
