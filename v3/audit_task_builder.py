"""Build compact, JSON-serializable section audit tasks.

The builder selects structured inputs for later model review. It does not call
a model and does not convert candidates into final findings.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


SECTIONS = ("Demo", "CC", "Hx", "Dx", "Inv", "Rx", "Adv")

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
    "Medication & Dosing": "Medication",
    "ANC/PNC Notes": "ANC_Notes",
}

_DEFAULT_FIELDS = {
    "Demo": ("Age", "Gender", "Diagnosis", "ANC_Notes"),
    "CC": (
        "Age",
        "Gender",
        "Chief_Complaint",
        "Health_Screening",
        "On_Examination",
        "Systemic_Examination",
        "ANC_Notes",
    ),
    "Hx": (
        "Age",
        "Gender",
        "Past_History",
        "Chief_Complaint",
        "Health_Screening",
        "On_Examination",
        "Systemic_Examination",
        "Investigation_Results",
        "ANC_Notes",
    ),
    "Dx": (
        "Age",
        "Gender",
        "Chief_Complaint",
        "Past_History",
        "Health_Screening",
        "On_Examination",
        "Systemic_Examination",
        "Investigation",
        "Investigation_Results",
        "ANC_Notes",
        "Diagnosis",
    ),
    "Inv": (
        "Age",
        "Gender",
        "Chief_Complaint",
        "Past_History",
        "Health_Screening",
        "On_Examination",
        "Systemic_Examination",
        "Diagnosis",
        "Investigation",
        "Investigation_Results",
        "ANC_Notes",
        "Advice",
    ),
    "Rx": (
        "Age",
        "Gender",
        "Chief_Complaint",
        "Past_History",
        "Health_Screening",
        "On_Examination",
        "Systemic_Examination",
        "Investigation",
        "Investigation_Results",
        "ANC_Notes",
        "Diagnosis",
        "Medication",
    ),
    "Adv": (
        "Age",
        "Gender",
        "Chief_Complaint",
        "Past_History",
        "Health_Screening",
        "On_Examination",
        "Systemic_Examination",
        "Investigation",
        "Investigation_Results",
        "ANC_Notes",
        "Diagnosis",
        "Medication",
        "Advice",
    ),
}

_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["section", "findings"],
    "properties": {
        "section": {"type": "string", "enum": list(SECTIONS)},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "rule_name",
                    "rule_id",
                    "why",
                    "correction",
                    "evidence",
                    "confidence",
                    "concern_level",
                ],
                "properties": {
                    "candidate_id": {"type": "string"},
                    "rule_name": {"type": "string"},
                    "rule_id": {"type": "string"},
                    "why": {"type": "string"},
                    "correction": {"type": "string"},
                    "evidence": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "concern_level": {
                        "type": "string",
                        "enum": ["critical", "major", "moderate", "minor"],
                    },
                },
            },
        },
    },
}

_BASE_INSTRUCTIONS = {
    "Demo": "Review only demographic candidates against the supplied demographic context.",
    "CC": (
        "Review patient-reported complaints semantically in Bengali, English, mixed language, transliteration, and common clinical shorthand; preserve the original meaning and use observed findings only as supporting context. "
        "Evaluate every supplied CC candidate independently and retain every separately supported concern. Do not suppress an age-context concern because an anatomical-detail concern is also present, and do not merge distinct concerns into one finding. "
        "For CC.context.demo, compare the complaint with recorded age, sex/gender, obstetric context, and any available relevant anatomy. Confirm only a genuine incompatibility or a highly implausible combination requiring verification. Do not assume administrative gender alone proves anatomy, and describe uncertain cases as needing clarification rather than impossible. "
        "For CC.2, do not demand the word mild/moderate/severe for every complaint. Confirm when intensity, measured value, grade, pattern, character, or functional impact is clinically important for interpreting or triaging that complaint and none is available elsewhere in the supplied current context. For fever, a contemporaneous measured temperature can satisfy characterization; fever with duration alone and no temperature, grade, pattern, or relevant characterization remains incomplete. "
        "For CC.4, confirm only when anatomical site, source, distribution, or laterality is clinically meaningful and missing. A paired or lateralized structure should identify right, left, or bilateral involvement when this affects interpretation; do not require laterality for diffuse or non-lateralized complaints. "
        "For CC.time.plausibility, distinguish impossible from merely unusual. Confirm an atypical or internally implausible timeframe only as a clarification concern, considering acute fluid change, measurement error, intermittent symptoms, and imprecise wording. Never claim medical impossibility unless the supplied context makes it unequivocal. "
        "For CC.context.conflict, confirm only a direct contradiction among current complaints, obstetric status, examination, or screening context. Compatible associated states are not contradictions: amenorrhea is ordinarily compatible with pregnancy. Consider chronology, negation, intermittent course, treatment, and improvement. "
        "For CC.hs, compare each active complaint with contemporaneous screening evidence. Confirm only when an objective observation clearly differs from a complaint that is presented as current and no timing, intermittent-course, treatment, or improvement context explains the difference. Describe it as a documentation inconsistency requiring clarification, not proof that the reported symptom is false. "
        "For CC.term.unclear, confirm only a clear typographical/transcription error or genuinely unrecognizable complaint expression. Accept understandable Bengali or English, transliteration, colloquial symptom wording, and common clinical shorthand. When confirmed and one correction is strongly supported, quote the erroneous text and write 'Did you mean: <corrected wording>?' in the correction. If no correction is reliable, give up to three possible interpretations and request clarification. Never silently replace the source text. "
        "For every CC.1 candidate, first determine the entry's literal meaning from Bengali, English, mixed language, or transliteration. Then ask whether it explicitly communicates an abnormal symptom, health problem, clinical concern, or valid encounter reason. If it does not, you MUST confirm CC.1. A person's name or pronoun followed only by an ordinary action such as eating, sleeping, walking, playing, or working is not a chief complaint unless an abnormal qualifier is stated (for example unable, reduced, excessive, painful, difficult, or changed). A duration does not convert a normal activity into a complaint. Accept genuine administrative encounter reasons such as report collection or follow-up. Do not invent an unstated appetite, dietary, gastrointestinal, or behavioral problem to make an ordinary sentence clinical. "
        "For CC.3, inspect the complaint text semantically and reject the candidate whenever a usable duration or onset is expressed, whether written as digits or words (for example, '5 days' or 'five days'), approximately ('a few days'), or as a bounded time expression such as today, yesterday, or since morning. Words such as recent, recently, new, longstanding, or for some time are temporally vague without a usable onset or duration and should be confirmed as a clarification concern."
    ),
    "Hx": (
        "Review free-text past history semantically in Bengali, English, mixed language, transliteration, and common clinical shorthand. Preserve the original wording and evaluate every supplied candidate independently. "
        "Past history is longitudinal context, not automatically a current diagnosis. A normal current examination does not by itself disprove a chronic or resolved condition. "
        "For Hx.term.unclear, confirm only genuinely unrecognizable, ambiguous, or clearly mistyped history text; do not rewrite understandable patient language for style. "
        "For Hx.context.demo, confirm only a genuine age, sex/gender, reproductive, or anatomical incompatibility, and request verification when uncertainty remains. "
        "For Hx.detail, request onset, status, stage, severity, site, laterality, procedure, allergen, or reaction only when that detail materially affects current diagnosis, investigation, medication safety, or follow-up. Do not require every historical condition to contain every attribute. "
        "For Hx.time.plausibility, distinguish an implausible history from an unusual but possible one and phrase uncertain cases as clarification needs. "
        "For Hx.context.conflict, confirm only a direct contradiction within the history or with current complaints, screening, examination, obstetric context, or actual results after considering active, resolved, intermittent, and treated states. "
        "For Hx.duplicate, flag only duplicate or conflicting entries that could mislead care; synonymous wording for the same condition should be consolidated rather than treated as two diseases."
    ),
    "Dx": (
        "Review each diagnosis against demographics and the complete supplied clinical context. ICD-11 validity establishes terminology, not patient-specific support. Avoid duplicate findings; retain the most specific rule. "
        "For Dx.term.unclear, confirm only a clear typographical/transcription error or genuinely unrecognizable diagnosis term. Accept valid ICD wording, parent/unspecified diagnoses, Bengali or English clinical wording, and established abbreviations. When one correction is strongly supported, write 'Did you mean: <corrected diagnosis>?'; otherwise request clarification and do not invent a diagnosis. "
        "For Dx.context.demo, confirm only a genuine biological, anatomical, age, or reproductive incompatibility. Uncommon is not impossible: male breast cancer is possible. Do not infer absent anatomy from gender alone; phrase uncertain cases as verification needs. "
        "For Dx.cc.1, confirm only when the diagnosis fails to account for the complaints taken together or names an explicitly conflicting anatomical site or organ system. Treat back/spine as axial anatomy and limbs/extremities as appendicular anatomy; do not treat them as interchangeable. Reject this candidate when the diagnosis is broader wording for the same site/system or otherwise plausibly explains the complaint. "
        "For Dx.hs.1, confirm when the diagnosis lacks reasonable support from symptoms, examination, or actual results, or directly contradicts that evidence after considering negation, timing, treatment, resolution, recurrence, and active versus past history. An ordered test without a result does not establish a diagnosis. For a serious definitive diagnosis, request supporting documentation or suspected-versus-confirmed clarification without inventing a diagnosis. "
        "For Dx.hs.2, do not require every abnormal sign to be repeated as a separate diagnosis. Reject the candidate when the entered diagnosis plausibly explains the finding or the finding is explicitly documented as under evaluation. Confirm only when a clinically significant active finding remains unexplained and unaddressed."
        "For Dx.vital.extreme, preserve the safety concern and request immediate value/unit verification and clinical assessment without inventing a diagnosis."
    ),
    "Inv": (
        "Review only supplied candidates; do not invent missing tests outside the supplied rulebook-check candidates (Inv.dx, Inv.cc, Inv.hs, Inv.demo.1, Inv.demo.2), which are the way to report a missing or unnecessary investigation. Interpret Bengali, English, mixed, transliterated, and imperfectly spelled context semantically. Judge every ordered item independently against all supplied demographics and clinical context. A catalogue match establishes identity and availability, not patient-specific relevance. "
        "For Inv.term.unrecognized, evaluate investigation identity separately from clinical relevance. An exact match explicitly marked Bangladesh catalogue catalog_exact is already a recognized source item and MUST be rejected as a terminology candidate; do not reinterpret abbreviations inside that source name as typos. Otherwise reject the candidate only when the entered item is confidently recognizable and unambiguous as a laboratory test, imaging study, diagnostic procedure, established abbreviation, or clearly understood local test name. Confirm a minor clarification finding when the text is an unfamiliar abbreviation, ambiguous local term, likely typographical error, or meaningless/non-investigation text, even if a probable intended test can be guessed. When one standard investigation is strongly supported, write 'Did you mean: <standard investigation>?'; otherwise give up to three possible interpretations and request clarification. Do not silently autocorrect or invent a replacement test. "
        "For Inv.relevance, first establish that the item is recognizable; do not use this rule for gibberish. Confirm only the individual candidate whose diagnostic purpose or organ system has no plausible link to the supplied context and no indication is documented; copy its candidate_id. Another test's relevance never establishes this item's relevance. Do not use absence from retrieved guidance alone. Treat broad tests such as CBC as potentially relevant to infection, inflammation, hematology, monitoring, or pre-treatment when context supports one of those purposes. If mismatch is clear, use a moderate finding requesting indication documentation; never call the test contraindicated."
    ),
    "Rx": (
        "Review treatment candidates using normalized medicines, relevant medicine facts, and active prior medicines. "
        "For Rx.term.unclear, use the mapper status and possible catalog matches. Confirm when the medicine identity remains unmatched or ambiguous; state the entered medicine and, when one catalog match is reliable, write 'Did you mean: <catalog medicine>?' Otherwise give the supported alternatives and request clarification rather than inventing a medicine. "
        "For any supplied Rx.indication.missing candidate, absence of a direct diagnosis or symptom match is not sufficient evidence of an error. Reject it when the medicine may plausibly be supportive, preventive, protective, adjunctive, symptomatic, or intended to manage another medicine's effects. Confirm only when supplied evidence explicitly contradicts the medicine choice; explicit guideline-avoid and contraindication findings use their own rules. "
        "For Rx.timing, compare the complete documented interval and direction with the retrieved administration evidence. A before/after-meal phrase does not satisfy a stated minimum interval when its explicit interval is shorter. "
        "For Rx.route.mismatch, confirm when the candidate supplies a dosage form and an explicitly different interpreted administration route, such as an oral tablet with eye or ear use."
    ),
    "Adv": (
        "Review only supplied advice candidates using diagnosis, medicine evidence, and patient context; absent advice with active treatment should remain a documentation concern. "
        "For Adv.missing, evaluate only patient advice, counselling, precautions, and safety-net instructions in the Advice field. PrescribeX records the follow-up plan in a separate Follow-up section, so do not state that follow-up guidance is missing and do not request follow-up documentation under Adv.missing."
        " For Adv.content.unrecognized, assess semantic validity and clear typographical errors, not whether every desirable counselling topic is present. Reject the candidate for any understandable patient instruction in Bengali or English, including concise diet, activity, hydration, medicine-use, precaution, return-warning, or safety-net advice. Confirm when the value is a person/name, random token, placeholder, meaningless non-instruction text, or contains a clear typo that changes or obscures meaning. When one correction is strongly supported, write 'Did you mean: <corrected advice>?' Otherwise request clarification without inventing advice."
    ),
}

_FACT_TYPES_BY_RULE = {
    "medicine_timing_missing_candidate": {"administration"},
    "medicine_timing_mismatch_candidate": {"administration"},
    "medicine_route_or_instruction_incomplete_candidate": {"administration"},
    "pregnancy_warning_candidate": {"pregnancy_lactation"},
    "pediatric_warning_candidate": {"pediatric_usage"},
    "ddi_candidate": {"interaction"},
    "contraindication_candidate": {"contraindication"},
    "administration_advice_candidate": {"administration"},
    "renal_risk_candidate": {"dosage", "contraindication", "precaution"},
    "hepatic_risk_candidate": {"dosage", "contraindication", "precaution"},
    "pregnancy_safety_candidate": {"pregnancy_lactation", "contraindication", "precaution"},
    "lactation_safety_candidate": {"pregnancy_lactation", "contraindication", "precaution"},
    "allergy_contraindication_candidate": {"contraindication", "precaution"},
    "pediatric_safety_candidate": {"pediatric_usage", "contraindication", "precaution"},
    "elderly_safety_candidate": {"dosage", "contraindication", "precaution"},
    "needs_weight_for_dose_review_candidate": {"dosage", "pediatric_usage"},
    "dose_exceeds_max_candidate": {"dosage"},
    "dose_exceeds_usual_or_max_candidate": {"dosage"},
    "high_daily_dose_review_candidate": {"dosage"},
    "medicine_strength_unusual_or_mismatch_candidate": {"dosage"},
    "indication_not_apparent_candidate": {"indication"},
}


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


def _canonical_name(name: Any) -> str:
    text = str(name or "").strip()
    return _ALIASES.get(text, text)


def _normalized_prescription(prescription: Mapping[str, Any]) -> dict[str, Any]:
    return {_canonical_name(key): value for key, value in prescription.items()}


def _protocol_value(protocol: Any, name: str, default: Any) -> Any:
    if isinstance(protocol, Mapping):
        return protocol.get(name, default)
    return getattr(protocol, name, default) if protocol is not None else default


def _section_fields(section: str, protocol: Any) -> tuple[str, ...]:
    configured = _protocol_value(protocol, "agent_fields", None)
    if configured is None:
        configured = _protocol_value(protocol, "fields", {})
    section_config = configured.get(section, ()) if isinstance(configured, Mapping) else ()
    combined = [_canonical_name(name) for name in _DEFAULT_FIELDS[section]]
    for name in section_config or ():
        canonical = _canonical_name(name)
        if canonical not in combined:
            combined.append(canonical)
    return tuple(combined)


def _fields_used(
    section: str,
    prescription: Mapping[str, Any],
    protocol: Any,
) -> dict[str, Any]:
    return {
        name: _json_safe(prescription.get(name, ""))
        for name in _section_fields(section, protocol)
    }


def _clinical_slice(
    section: str,
    clinical_evidence: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    allowed_sources = {
        "Demo": {"Health_Screening", "On_Examination", "Systemic_Examination"},
        "CC": {"Chief_Complaint", "Health_Screening", "On_Examination", "Systemic_Examination"},
        "Hx": {"Past_History", "Chief_Complaint", "Health_Screening", "On_Examination", "Systemic_Examination", "Investigation_Results"},
        "Dx": {"Past_History", "Chief_Complaint", "Health_Screening", "On_Examination", "Systemic_Examination"},
        "Inv": {"Past_History", "Chief_Complaint", "Health_Screening", "On_Examination", "Systemic_Examination"},
        "Rx": {"Past_History", "Chief_Complaint", "Health_Screening", "On_Examination", "Systemic_Examination"},
        "Adv": {"Past_History", "Chief_Complaint", "Health_Screening", "On_Examination", "Systemic_Examination"},
    }[section]
    return [
        _json_safe(item)
        for item in clinical_evidence
        if str(item.get("source", "")) in allowed_sources
    ]


def _context_slice(section: str, patient_context: Mapping[str, Any]) -> dict[str, Any]:
    if section in {"CC", "Hx"}:
        keys = (
            "age_years", "gender", "is_child", "is_elderly", "is_pregnant",
            "is_lactating", "context_evidence", "warnings",
        )
    elif section == "Demo":
        keys = (
            "age_years", "gender", "is_child", "is_elderly", "is_pregnant",
            "is_lactating", "context_evidence", "warnings",
        )
    elif section in {"Rx", "Adv"}:
        return _json_safe(patient_context)
    else:
        keys = (
            "age_years", "gender", "is_child", "is_elderly", "is_pregnant",
            "is_lactating", "active_conditions", "warnings",
        )
    return {
        key: _json_safe(patient_context[key])
        for key in keys
        if key in patient_context
    }


def _candidate_fact_selectors(
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, list[tuple[str, str]]]:
    selectors: dict[str, list[tuple[str, str]]] = {}
    for candidate in candidates:
        generic_id = str(candidate.get("generic_id", "") or "").strip()
        if not generic_id:
            continue
        source_field = str(candidate.get("source_field", "") or "").strip().casefold()
        evidence_values = []
        for key in ("medicine_fact_evidence", "medicine_fact_evidences"):
            value = candidate.get(key)
            if isinstance(value, list):
                evidence_values.extend(str(item or "").strip().casefold() for item in value)
            elif value:
                evidence_values.append(str(value or "").strip().casefold())
        for evidence in evidence_values:
            if evidence:
                selectors.setdefault(generic_id, []).append(("text", evidence))
        if evidence_values:
            continue
        if source_field:
            selectors.setdefault(generic_id, []).append(("source_field", source_field))
            continue
        rule_name = str(candidate.get("rule_name", "") or "").strip()
        for fact_type in _FACT_TYPES_BY_RULE.get(rule_name, set()):
            selectors.setdefault(generic_id, []).append(("fact_type", fact_type.casefold()))
    return selectors


def _relevant_medicine_facts(
    section: str,
    candidates: Sequence[Mapping[str, Any]],
    medicines: Sequence[Mapping[str, Any]],
    medicine_facts: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    del medicines  # Relevance comes from candidates, never unrelated prescribed drugs.
    if section not in {"Rx", "Adv"}:
        return []
    selectors = _candidate_fact_selectors(candidates)
    if not selectors:
        return []

    results: list[dict[str, Any]] = []
    for result in medicine_facts:
        generic_id = str(result.get("generic_id", "") or "").strip()
        fact_section = str(result.get("section", "") or "").strip()
        if generic_id not in selectors:
            continue
        has_explicit_source = any(
            kind == "source_field" for kind, _ in selectors[generic_id]
        )
        if fact_section and fact_section != section and not has_explicit_source:
            continue
        selected_facts = []
        for fact in result.get("facts", []) or []:
            if not isinstance(fact, Mapping):
                continue
            source_field = str(fact.get("source_field", "") or "").strip().casefold()
            fact_type = str(fact.get("fact_type", "") or "").strip().casefold()
            fact_text = str(fact.get("text", "") or "").strip().casefold()
            if any(
                (kind == "text" and value == fact_text)
                or (kind == "source_field" and value == source_field)
                or (kind == "fact_type" and value == fact_type)
                for kind, value in selectors[generic_id]
            ):
                selected_facts.append(_json_safe(fact))
        if selected_facts:
            selected = _json_safe(result)
            selected["facts"] = selected_facts
            results.append(selected)
    return results


def _medicine_matches_candidate(
    medicine: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> bool:
    """Return true when a medicine is directly tied to a candidate.

    This keeps Rx/Adv prompts small: a one-medicine candidate should not carry
    every other normalized medicine in the prescription. Matching is exact or
    evidence-substring based; it does not infer new medicine identities.
    """
    candidate_generic_id = str(candidate.get("generic_id", "") or "").strip()
    medicine_generic_id = str(medicine.get("generic_id", "") or "").strip()
    if candidate_generic_id and medicine_generic_id == candidate_generic_id:
        return True

    candidate_text = " ".join(
        str(candidate.get(key, "") or "")
        for key in ("evidence", "generic_name", "brand_name", "medicine_name")
    ).casefold()
    if not candidate_text.strip():
        return False
    for key in ("raw_text", "generic_name", "brand_name"):
        value = str(medicine.get(key, "") or "").strip()
        if value and value.casefold() in candidate_text:
            return True
    return False


def _relevant_medicines(
    section: str,
    candidates: Sequence[Mapping[str, Any]],
    medicines: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if section not in {"Rx", "Adv"}:
        return []
    selected: list[dict[str, Any]] = []
    for medicine in medicines:
        if any(_medicine_matches_candidate(medicine, candidate) for candidate in candidates):
            selected.append(_json_safe(medicine))
    if selected:
        return selected
    # Some broad guideline candidates are condition-level rather than
    # medicine-specific; keep the previous behavior only for those cases.
    return [_json_safe(medicine) for medicine in medicines]


def _followup_slice(section: str, followup_context: Mapping[str, Any]) -> dict[str, Any]:
    if section == "Inv":
        keys = (
            "prior_visits_checked",
            "prior_current_results",
            "prior_ordered_not_done_tests",
            "warnings",
        )
    elif section == "Rx":
        keys = ("prior_visits_checked", "active_prior_medicines", "warnings")
    elif section == "Adv":
        keys = (
            "prior_visits_checked",
            "active_prior_medicines",
            "prior_ordered_not_done_tests",
            "warnings",
        )
    else:
        return {}
    return {
        key: _json_safe(followup_context[key])
        for key in keys
        if key in followup_context
    }


def _relevant_protocol_rules(
    section: str,
    candidates: Sequence[Mapping[str, Any]],
    protocol: Any,
) -> list[dict[str, str]]:
    concerns = _protocol_value(protocol, "concerns", {})
    rules = concerns.get(section, []) if isinstance(concerns, Mapping) else []
    wanted_ids = {str(item.get("rule_id", "")) for item in candidates}
    selected = []
    for rule in rules:
        if not isinstance(rule, Mapping):
            continue
        rule_id = str(rule.get("id", ""))
        if rule_id in wanted_ids:
            selected.append({"rule_id": rule_id, "rule": str(rule.get("concern", ""))})
    return selected


def _instructions(
    section: str,
    candidates: Sequence[Mapping[str, Any]],
    protocol: Any,
) -> str:
    lines = [
        _BASE_INSTRUCTIONS[section],
        "Confirm or reject each supplied candidate using only the task evidence.",
        "Return an empty findings array when no candidate is confirmed; do not invent new facts.",
        "For each finding write the correction as a specific action for that rule (what the clinician should check or change). "
        "Use 'Did you mean: ...?' only for term/unrecognized/unclear rules, never for mismatch or missing-item rules.",
    ]
    rules = _relevant_protocol_rules(section, candidates, protocol)
    if any(item.get("trigger_source") == "protocol_review" for item in candidates):
        lines.append(
            "Candidates with trigger_source protocol_review are rulebook checks: apply the rule text below exactly "
            "and confirm only when the supplied evidence clearly violates it; omit the candidate when the "
            "evidence is acceptable or insufficient."
        )
    if rules:
        lines.append("Relevant protocol rules:")
        lines.extend(f"- [{rule['rule_id']}] {rule['rule']}" for rule in rules)
    return "\n".join(lines)


def build_audit_tasks(
    prescription_id: Any,
    prescription: Mapping[str, Any],
    clinical_evidence: Sequence[Mapping[str, Any]],
    patient_context: Mapping[str, Any],
    normalized_medicines: Sequence[Mapping[str, Any]],
    medicine_facts: Sequence[Mapping[str, Any]],
    followup_context: Mapping[str, Any],
    candidates_by_section: Mapping[str, Sequence[Mapping[str, Any]]],
    protocol: Any = None,
    *,
    include_all_sections: bool = False,
) -> dict[str, Any]:
    """Build compact tasks for candidate sections, preserving section order."""
    normalized = _normalized_prescription(prescription)
    grouped = {
        section: list(candidates_by_section.get(section, []) or [])
        for section in SECTIONS
    }
    has_candidates = any(grouped.values())
    tasks: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    warnings: list[str] = []

    if not has_candidates:
        return {
            "tasks": [],
            "skipped_sections": [
                {"section": section, "reason": "no_candidates"} for section in SECTIONS
            ],
            "warnings": [],
        }

    for section in SECTIONS:
        candidates = grouped[section]
        if not candidates and not include_all_sections:
            skipped.append({"section": section, "reason": "no_candidates"})
            continue
        task: dict[str, Any] = {
            "prescription_id": str(prescription_id),
            "section": section,
            "fields_used": _fields_used(section, normalized, protocol),
            "clinical_evidence": _clinical_slice(section, clinical_evidence),
            "patient_context": _context_slice(section, patient_context),
            "candidates": _json_safe(candidates),
            "medicine_facts": _relevant_medicine_facts(
                section, candidates, normalized_medicines, medicine_facts
            ),
            "followup_context": _followup_slice(section, followup_context),
            "instructions": _instructions(section, candidates, protocol),
            "output_schema": _json_safe(_OUTPUT_SCHEMA),
        }
        if section in {"Rx", "Adv"}:
            task["normalized_medicines"] = _relevant_medicines(
                section, candidates, normalized_medicines
            )
        tasks.append(task)

    for section in candidates_by_section:
        if section not in SECTIONS:
            warnings.append(f"unsupported candidate section was ignored: {section}")
    return {"tasks": tasks, "skipped_sections": skipped, "warnings": warnings}


__all__ = ["SECTIONS", "build_audit_tasks"]
