"""End-to-end orchestration for one V3 prescription audit.

This module only connects the existing evidence, candidate, model-writing, and
final-report stages. Clinical decisions remain owned by those individual stages.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Mapping, Sequence

import yaml

from .audit_task_builder import build_audit_tasks
from .audit_writer import write_audit_sections
from .clinical_finding_interpreter import interpret_clinical_findings
from .final_report import assemble_final_report
from .followup_context_engine import build_followup_context
from .guideline_rag import GuidelineRAG
from .guideline_rag_router import (
    build_guideline_rag_seeds,
    retrieve_guideline_facts_for_seeds,
)
from .medicine_facts import MedicineFactStore
from .medicine_mapper import MedicineMapper
from .patient_context import build_patient_context
from .prescription_language import normalize_prescription_language
from .protocol import load_protocol, resolve_protocol_path
from .rag_query_planner import plan_guideline_rag_seeds
from .rule_candidate_engine import generate_rule_candidates
from .safety_candidate_engine import generate_safety_candidates


_FACT_SECTIONS = ("Rx", "Adv", "Dx")
_STANDARD_ALIASES = {
    "prescription id": "Prescription_ID",
    "id": "Prescription_ID",
    "patient id": "Patient_ID",
    "visit date": "Visit_Date",
    "visit datetime": "Visit_Date",
    "date": "Visit_Date",
    "age": "Age",
    "gender": "Gender",
    "chief complaint": "Chief_Complaint",
    "chief complain": "Chief_Complaint",
    "past history": "Past_History",
    "medical history": "Past_History",
    "health screening": "Health_Screening",
    "on examination": "On_Examination",
    "systemic examination": "Systemic_Examination",
    "investigation": "Investigation",
    "investigations": "Investigation",
    "investigation result": "Investigation_Results",
    "investigation results": "Investigation_Results",
    "diagnosis": "Diagnosis",
    "medication": "Medication",
    "medications": "Medication",
    "medication & dosing": "Medication",
    "advice": "Advice",
    "anc/pnc notes": "ANC_Notes",
    "anc notes": "ANC_Notes",
}


@dataclass(frozen=True, slots=True)
class AuditOptions:
    prescription_id: Any = None
    protocol_path: str | Path | None = None
    medicine_csv_path: str | Path | None = None
    generic_csv_path: str | Path | None = None
    annotated_facts_path: str | Path | None = None
    followup_db_path: str | Path | None = None
    patient_id: Any = None
    visit_date: Any = None
    include_debug: bool = False
    include_all_sections: bool = False
    llm_advice_action_classifier: bool = False
    llm_rag_router: bool = False
    provider: Any = None
    max_retries: int = 2
    max_tokens: int = 1000
    target_sections: tuple[str, ...] | None = None
    live_section_audit: bool = False


def _option_key(value: Any) -> str:
    return " ".join(str(value or "").strip().replace("_", " ").split()).casefold()


def standardize_prescription(row: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize known CSV/app field aliases while preserving unknown fields."""
    if not isinstance(row, Mapping):
        raise TypeError("prescription row must be a mapping")
    standardized: dict[str, Any] = {}
    for key, value in row.items():
        raw_key = str(key or "").strip()
        canonical = _STANDARD_ALIASES.get(_option_key(raw_key), raw_key)
        normalized_value = "" if value is None else value
        if canonical not in standardized or not str(standardized[canonical]).strip():
            standardized[canonical] = normalized_value
    return standardized


def _prune_candidates_for_model(
    candidates_by_section: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    pruned = {
        section: [dict(item) for item in candidates_by_section.get(section, []) or []]
        for section in ("Demo", "CC", "Hx", "Dx", "Inv", "Rx", "Adv")
    }
    rx_timing_keys = {
        (
            str(item.get("generic_id", "") or ""),
            str(item.get("generic_name", "") or "").casefold(),
        )
        for item in pruned["Rx"]
        if item.get("rule_name") in {
            "medicine_timing_missing_candidate",
            "medicine_timing_mismatch_candidate",
        }
    }
    if rx_timing_keys:
        pruned["Adv"] = [
            item for item in pruned["Adv"]
            if not (
                item.get("rule_name") == "administration_advice_candidate"
                and (
                    str(item.get("generic_id", "") or ""),
                    str(item.get("generic_name", "") or "").casefold(),
                )
                in rx_timing_keys
            )
        ]

    pruned["Dx"] = [
        item for item in pruned["Dx"]
        if not (
            item.get("rule_name") == "guideline_diagnosis_review_candidate"
            and str(item.get("evidence_strength", "") or "low") == "low"
            and str(item.get("must_review_if_rejected", "")).casefold() != "true"
        )
    ]
    for section in ("Rx", "Adv"):
        pruned[section] = [
            item for item in pruned[section]
            if not (
                str(item.get("trigger_source", "")) == "guideline_rag"
                and str(item.get("evidence_strength", "") or "low") == "low"
                and str(item.get("importance", "")) not in {"critical_review", "high"}
                and str(item.get("must_review_if_rejected", "")).casefold() != "true"
            )
        ]
    return pruned


def _coerce_options(options: AuditOptions | Mapping[str, Any] | None) -> AuditOptions:
    if options is None:
        return AuditOptions()
    if isinstance(options, AuditOptions):
        return options
    if isinstance(options, Mapping):
        return AuditOptions(**dict(options))
    raise TypeError("options must be AuditOptions, a mapping, or None")


def _resolve_data_path(path: str | Path | None, filename: str) -> Path:
    if path is not None:
        source = Path(path).expanduser().resolve()
        if source.is_file():
            return source
        raise FileNotFoundError(f"Configured {filename} was not found: {source}")
    package_dir = Path(__file__).resolve().parent
    candidates = (
        package_dir.parent / "data" / filename,
        package_dir / "data" / filename,
    )
    for source in candidates:
        if source.is_file():
            return source
    searched = "\n  - ".join(str(source) for source in candidates)
    raise FileNotFoundError(
        f"No {filename} was found. Provide its AuditOptions path or place it at:\n"
        f"  - {searched}"
    )


def _protocol_config(path: str | Path | None) -> dict[str, Any]:
    source = resolve_protocol_path(path)
    parsed = load_protocol(source)
    with source.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    config = dict(raw) if isinstance(raw, Mapping) else {}
    config.update(
        {
            "role": parsed.role,
            "section_names": parsed.section_names,
            "fields": parsed.fields,
            "agent_fields": parsed.fields,
            "concerns": parsed.concerns,
            "followup_only": parsed.followup_only,
            "order": parsed.order,
        }
    )
    monitoring = config.get("monitoring_investigations", [])
    if monitoring and not config.get("monitoring_exemptions"):
        config["monitoring_exemptions"] = [
            {
                "diagnosis_keywords": [item.get("diagnosis_keyword", "")],
                "investigations": [item.get("test", "")],
            }
            for item in monitoring
            if isinstance(item, Mapping)
            and item.get("diagnosis_keyword")
            and item.get("test")
        ]
    return config


def _empty_followup_context() -> dict[str, Any]:
    return {
        "prior_visits_checked": 0,
        "active_prior_medicines": [],
        "prior_current_results": [],
        "prior_ordered_not_done_tests": [],
        "candidates": [],
        "warnings": [],
    }


def _medicine_facts_trace(
    medicine_facts: list[dict[str, Any]], *, full: bool
) -> Any:
    if full:
        return {"mode": "full", "results": medicine_facts}
    summaries = []
    for result in medicine_facts:
        facts = result.get("facts", []) if isinstance(result, Mapping) else []
        summaries.append(
            {
                "generic_id": str(result.get("generic_id", "")),
                "generic_name": str(result.get("generic_name", "")),
                "section": str(result.get("section", "")),
                "fact_count": len(facts),
                "fact_types": sorted(
                    {str(fact.get("fact_type", "")) for fact in facts if isinstance(fact, Mapping) and fact.get("fact_type")}
                ),
                "source_types": sorted(
                    {str(fact.get("source_type", "")) for fact in facts if isinstance(fact, Mapping) and fact.get("source_type")}
                ),
                "warnings": list(result.get("warnings", [])),
            }
        )
    return {"mode": "summary", "results": summaries}


def _redact_medicine_fact_evidence(value: Any, *, full: bool) -> Any:
    if full:
        return value
    if isinstance(value, Mapping):
        result = {
            str(key): _redact_medicine_fact_evidence(item, full=full)
            for key, item in value.items()
        }
        if result.get("trigger_source") == "medicine_fact":
            for field in ("evidence", "medicine_fact_evidence"):
                if not result.get(field):
                    continue
                evidence = str(result[field])
                result[field] = "[medicine fact text omitted; use --trace-full]"
                result[f"{field}_character_count"] = len(evidence)
        return result
    if isinstance(value, (list, tuple)):
        return [_redact_medicine_fact_evidence(item, full=full) for item in value]
    return value


def _patient_context_trace(
    context: Mapping[str, Any], *, full: bool
) -> dict[str, Any]:
    if full:
        return dict(context)
    result = dict(context)
    risks = [
        item for item in context.get("patient_risks", []) or []
        if isinstance(item, Mapping)
    ]
    result.pop("patient_risks", None)
    result["patient_risk_summary"] = {
        "count": len(risks),
        "risk_types": sorted({
            str(item.get("risk_type", "")) for item in risks
            if item.get("risk_type")
        }),
        "evidence": [
            {
                key: (str(item.get(key, ""))[:240] if key == "evidence" else item.get(key))
                for key in ("risk_type", "source", "evidence", "value", "unit", "substance")
                if item.get(key) not in (None, "")
            }
            for item in risks
        ],
    }
    return result


def _followup_trace(context: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "current_patient_id": str(context.get("current_patient_id", "") or ""),
        "current_visit_date": str(context.get("current_visit_date", "") or ""),
        "prior_visits_checked": int(context.get("prior_visits_checked", 0) or 0),
        "active_prior_medicines": list(context.get("active_prior_medicines", []) or []),
        "prior_current_results": list(context.get("prior_current_results", []) or []),
        "prior_ordered_not_done_tests": list(context.get("prior_ordered_not_done_tests", []) or []),
        "current_results_matching_prior_orders": list(context.get("current_results_matching_prior_orders", []) or []),
        "followup_candidates": list(context.get("candidates", []) or []),
        "candidate_count": len(context.get("candidates", []) or []),
        "warnings": list(context.get("warnings", []) or []),
    }


def _task_trace(task_result: Mapping[str, Any]) -> dict[str, Any]:
    tasks = list(task_result.get("tasks", []) or [])
    task_summaries = []
    for task in tasks:
        fact_count = 0
        fact_char_count = 0
        for result in task.get("medicine_facts", []) or []:
            if not isinstance(result, Mapping):
                continue
            for fact in result.get("facts", []) or []:
                if not isinstance(fact, Mapping):
                    continue
                fact_count += 1
                fact_char_count += len(str(fact.get("text", "") or ""))
        task_summaries.append(
            {
                "section": str(task.get("section", "")),
                "candidate_count": len(task.get("candidates", []) or []),
                "medicine_fact_count": fact_count,
                "medicine_fact_char_count": fact_char_count,
            }
        )
    return {
        "task_count": len(tasks),
        "sections": [str(task.get("section", "")) for task in tasks],
        "candidate_counts": {
            str(task.get("section", "")): len(task.get("candidates", []) or [])
            for task in tasks
        },
        "tasks": task_summaries,
        "skipped_sections": list(task_result.get("skipped_sections", []) or []),
        "warnings": list(task_result.get("warnings", []) or []),
    }


def _guideline_rag_trace(guideline_facts_by_section: Mapping[str, Any], *, full: bool) -> dict[str, Any]:
    sections: dict[str, Any] = {}
    planner = guideline_facts_by_section.get("_planner", {}) if isinstance(guideline_facts_by_section, Mapping) else {}
    for section, payload in (guideline_facts_by_section or {}).items():
        if str(section).startswith("_"):
            continue
        if not isinstance(payload, Mapping):
            continue
        facts = [fact for fact in payload.get("facts", []) or [] if isinstance(fact, Mapping)]
        sections[str(section)] = {
            "fact_count": len(facts),
            "matched_conditions": list(payload.get("matched_conditions", []) or []),
            "retrieval_seeds": list(payload.get("retrieval_seeds", []) or []),
            "warnings": list(payload.get("warnings", []) or []),
        }
        if full:
            sections[str(section)]["facts"] = facts
    result: dict[str, Any] = {"mode": "full" if full else "summary", "sections": sections}
    if planner:
        result["llm_rag_router"] = planner
    return result


def _writer_trace(result: Mapping[str, Any]) -> dict[str, Any]:
    outputs = result.get("section_outputs", {})
    model_calls = [call for call in result.get("model_calls", []) or [] if isinstance(call, Mapping)]
    section_durations: dict[str, float] = {}
    for call in model_calls:
        section = str(call.get("section", "") or "")
        if not section:
            continue
        try:
            section_durations[section] = section_durations.get(section, 0.0) + float(
                call.get("duration_ms", 0) or 0
            )
        except (TypeError, ValueError):
            continue
    return {
        "sections": list(outputs) if isinstance(outputs, Mapping) else [],
        "model_calls": model_calls,
        "model_call_summary": {
            "total_model_duration_ms": round(sum(section_durations.values()), 3),
            "section_duration_ms": {
                section: round(duration, 3)
                for section, duration in section_durations.items()
            },
            "section_call_counts": {
                section: sum(1 for call in model_calls if str(call.get("section", "") or "") == section)
                for section in section_durations
            },
        },
        "warnings": list(result.get("warnings", []) or []),
    }


def _final_report_trace(report: Mapping[str, Any]) -> dict[str, Any]:
    errors = report.get("errors_by_section", {})
    metadata = report.get("metadata", {})
    return {
        "prescription_id": str(report.get("prescription_id", "")),
        "status": str(report.get("status", "")),
        "candidate_count": int(metadata.get("candidate_count", 0) or 0),
        "final_finding_count": int(metadata.get("final_finding_count", 0) or 0),
        "findings_by_section": {
            str(section): len(findings or [])
            for section, findings in errors.items()
        },
        "model_sections_called": list(metadata.get("model_sections_called", []) or []),
        "warnings": list(metadata.get("warnings", []) or []),
    }


def _medicine_mapping_trace(
    medicines: Sequence[Mapping[str, Any]],
    safety_candidates: Mapping[str, Any],
) -> list[dict[str, Any]]:
    dose_candidate_raws = {
        str(candidate.get("evidence", ""))
        for candidate in safety_candidates.get("candidates", [])
        if str(candidate.get("rule_name", "")).startswith(
            (
                "dose_exceeds",
                "high_daily_dose",
                "needs_weight_for_dose",
            )
        )
    }
    result: list[dict[str, Any]] = []
    for medicine in medicines:
        raw_text = str(medicine.get("raw_text", ""))
        item = dict(medicine)
        item["candidate_matches_count"] = len(medicine.get("candidate_matches", []) or [])
        item["dose_summary"] = {
            "raw_medicine": raw_text,
            "raw_frequency": medicine.get("raw_frequency"),
            "normalized_frequency": medicine.get("normalized_frequency"),
            "frequency_type": medicine.get("frequency_type"),
            "frequency_cache_hit": medicine.get("frequency_cache_hit"),
            "cached_translation": medicine.get("cached_translation"),
            "calculation_allowed": medicine.get("calculation_allowed"),
            "no_calculation_reason": medicine.get("no_calculation_reason"),
            "strength_mg": medicine.get("strength_mg"),
            "units_per_day": medicine.get("units_per_day"),
            "calculated_daily_dose_mg": medicine.get("calculated_daily_dose_mg"),
            "parse_confidence": medicine.get("parse_confidence"),
            "reason": medicine.get("dose_calculation_reason"),
            "dose_candidate_created": raw_text in dose_candidate_raws,
        }
        result.append(item)
    return result


def _build_trace_document(
    prescription_id: Any,
    prescription: Mapping[str, Any],
    clinical_evidence: list[dict[str, Any]],
    language_context: Mapping[str, Any],
    patient_context: Mapping[str, Any],
    normalized_medicines: list[dict[str, Any]],
    planner_trace: Mapping[str, Any],
    medicine_facts: list[dict[str, Any]],
    safety_candidates: Mapping[str, Any],
    followup_context: Mapping[str, Any],
    guideline_facts_by_section: Mapping[str, Any],
    rule_result: Mapping[str, Any],
    task_result: Mapping[str, Any],
    writer_result: Mapping[str, Any],
    final_report: Mapping[str, Any],
    stage_durations: Mapping[str, float],
    total_duration_ms: float,
    *,
    trace_full: bool,
) -> dict[str, Any]:
    def stage(number: int, name: str, data: Any) -> dict[str, Any]:
        return {
            "stage": number,
            "name": name,
            "duration_ms": float(stage_durations.get(name, 0.0)),
            "data": data,
        }

    return {
        "prescription_id": str(prescription_id),
        "trace_full": trace_full,
        "total_duration_ms": total_duration_ms,
        "timing_summary": {
            "total_duration_ms": total_duration_ms,
            "stage_duration_ms": dict(stage_durations),
        },
        "stages": [
            stage(1, "standard_prescription", dict(prescription)),
            stage(2, "clinical_evidence", clinical_evidence),
            stage(3, "language_normalization", language_context if trace_full else {
                "field_count": len(language_context.get("fields", {})),
                "evidence_count": len(language_context.get("clinical_evidence", [])),
            }),
            stage(
                4,
                "patient_context",
                _patient_context_trace(patient_context, full=trace_full),
            ),
            stage(
                5,
                "medicine_mapping",
                _medicine_mapping_trace(normalized_medicines, safety_candidates),
            ),
            stage(
                6,
                "rag_router",
                planner_trace,
            ),
            stage(
                7,
                "medicine_facts",
                _medicine_facts_trace(medicine_facts, full=trace_full),
            ),
            stage(
                8,
                "guideline_rag",
                _guideline_rag_trace(guideline_facts_by_section, full=trace_full),
            ),
            stage(
                9,
                "safety_candidates",
                _redact_medicine_fact_evidence(
                    safety_candidates, full=trace_full
                ),
            ),
            stage(10, "followup_context", _followup_trace(followup_context)),
            stage(
                11,
                "rule_candidates",
                _redact_medicine_fact_evidence(rule_result, full=trace_full),
            ),
            stage(12, "audit_tasks", _task_trace(task_result)),
            stage(13, "audit_writer/model_call", _writer_trace(writer_result)),
            stage(14, "final_report", _final_report_trace(final_report)),
        ],
    }


class PrescriptionAuditOrchestrator:
    """Cache static V3 resources and audit one or many prescription rows."""

    def __init__(
        self,
        options: AuditOptions | Mapping[str, Any] | None = None,
        *,
        provider: Any = None,
    ) -> None:
        self.options = _coerce_options(options)
        self.provider = provider if provider is not None else self.options.provider
        self.protocol = _protocol_config(self.options.protocol_path)
        self.medicine_csv_path = _resolve_data_path(
            self.options.medicine_csv_path, "medicine.csv"
        )
        self.generic_csv_path = _resolve_data_path(
            self.options.generic_csv_path, "generic.csv"
        )
        self.medicine_mapper = MedicineMapper(
            self.medicine_csv_path, self.generic_csv_path
        )
        self.fact_store = MedicineFactStore(
            self.generic_csv_path, self.options.annotated_facts_path
        )
        self.guideline_rag = GuidelineRAG()

    def audit_prescription(
        self,
        row: Mapping[str, Any],
        options: AuditOptions | Mapping[str, Any] | None = None,
        *,
        trace_callback: Callable[[Mapping[str, Any]], None] | None = None,
        trace_full: bool = False,
    ) -> dict[str, Any]:
        audit_started = perf_counter()
        stage_durations: dict[str, float] = {}

        def finish_stage(name: str, started: float) -> None:
            stage_durations[name] = round((perf_counter() - started) * 1000, 3)

        def timed_stage(name: str, fn):
            started = perf_counter()
            result = fn()
            finish_stage(name, started)
            return result

        effective = self.options if options is None else _coerce_options(options)
        target_sections = (
            tuple(dict.fromkeys(str(section) for section in effective.target_sections))
            if effective.target_sections
            else None
        )
        if target_sections:
            invalid = [section for section in target_sections if section not in ("Demo", "CC", "Hx", "Dx", "Inv", "Rx", "Adv")]
            if invalid:
                raise ValueError(f"invalid target audit section(s): {', '.join(invalid)}")

        started = perf_counter()
        prescription = standardize_prescription(row)
        prescription_id = (
            effective.prescription_id
            if effective.prescription_id not in (None, "")
            else prescription.get("Prescription_ID", "")
        )
        finish_stage("standard_prescription", started)

        started = perf_counter()
        clinical_evidence = interpret_clinical_findings(prescription)
        finish_stage("clinical_evidence", started)

        started = perf_counter()
        language_context = normalize_prescription_language(prescription, clinical_evidence)
        finish_stage("language_normalization", started)

        started = perf_counter()
        patient_context = build_patient_context(prescription, clinical_evidence)
        finish_stage("patient_context", started)

        started = perf_counter()
        normalized_medicines = self.medicine_mapper.map(
            prescription.get("Medication", "")
        )
        finish_stage("medicine_mapping", started)

        started = perf_counter()
        guideline_seeds = build_guideline_rag_seeds(
            prescription,
            normalized_medicines,
            clinical_evidence,
        )
        if target_sections:
            # A live section audit should not retrieve evidence for domains that
            # will be discarded later.  The complete final audit has no target
            # filter and continues to retrieve all applicable domains.
            allowed_seed_sections = set(target_sections)
            guideline_seeds = [
                seed for seed in guideline_seeds
                if seed.section in allowed_seed_sections
            ]
        if effective.live_section_audit and target_sections == ("Dx",):
            # Live diagnosis checks judge the entered diagnosis against the
            # structured current context already carried by the task. Guideline
            # retrieval does not establish patient-specific diagnostic support
            # and remains available in the complete final audit.
            guideline_seeds = []
        planner_trace: dict[str, Any] = {
            "enabled": False,
            "used": False,
            "warnings": [],
            "plans": [],
            "model_call": None,
        }
        if effective.llm_rag_router:
            active_provider = (
                effective.provider
                if effective.provider is not None
                else self.provider
            )
            if active_provider is None:
                planner_trace["enabled"] = True
                planner_trace["warnings"] = [
                    "llm_rag_router requested but no provider is available; deterministic seeds used"
                ]
            else:
                planned = plan_guideline_rag_seeds(
                    prescription=prescription,
                    seeds=guideline_seeds,
                    provider=active_provider,
                    clinical_evidence=clinical_evidence,
                    language_context=language_context,
                )
                guideline_seeds = planned["seeds"]
                planner_trace = {
                    "enabled": True,
                    "used": bool(planned.get("model_call", {}).get("success")),
                    "warnings": list(planned.get("warnings", []) or []),
                    "plans": list(planned.get("plans", []) or []),
                    "model_call": planned.get("model_call"),
                }
        finish_stage("rag_router", started)

        def load_medicine_facts():
            return [
                self.fact_store.get_facts(medicine, section)
                for medicine in normalized_medicines
                for section in _FACT_SECTIONS
            ]

        def load_guideline_facts():
            facts_by_section = retrieve_guideline_facts_for_seeds(
                self.guideline_rag,
                guideline_seeds,
            )
            facts_by_section["_planner"] = planner_trace
            return facts_by_section

        with ThreadPoolExecutor(max_workers=2) as executor:
            medicine_facts_future = executor.submit(
                timed_stage, "medicine_facts", load_medicine_facts
            )
            guideline_facts_future = executor.submit(
                timed_stage, "guideline_rag", load_guideline_facts
            )
            medicine_facts = medicine_facts_future.result()
            guideline_facts_by_section = guideline_facts_future.result()

        def build_safety_candidates():
            return generate_safety_candidates(
                normalized_medicines,
                medicine_facts,
                patient_context,
                str(prescription.get("Advice", "") or ""),
                prescription_context=prescription,
            )

        def build_followup_context_stage():
            if effective.followup_db_path is None:
                return _empty_followup_context()
            patient_id = (
                effective.patient_id
                if effective.patient_id not in (None, "")
                else prescription.get("Patient_ID", "")
            )
            visit_date = (
                effective.visit_date
                if effective.visit_date not in (None, "")
                else prescription.get("Visit_Date", "")
            )
            return build_followup_context(
                patient_id,
                visit_date,
                prescription,
                normalized_medicines,
                db_path=effective.followup_db_path,
                lookback_days=int(self.protocol.get("followup_lookback_days", 30)),
                buffer_days=int(self.protocol.get("followup_buffer_days", 0)),
                medicine_mapper=self.medicine_mapper,
                today_medicine_facts=medicine_facts,
                medicine_fact_lookup=lambda medicine: [
                    self.fact_store.get_facts(medicine, section)
                    for section in _FACT_SECTIONS
                ],
                advice_action_provider=self.provider
                if effective.llm_advice_action_classifier
                else None,
                protocol=self.protocol,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            safety_future = executor.submit(
                timed_stage, "safety_candidates", build_safety_candidates
            )
            followup_future = executor.submit(
                timed_stage, "followup_context", build_followup_context_stage
            )
            safety_candidates = safety_future.result()
            followup_context = followup_future.result()

        started = perf_counter()
        rule_result = generate_rule_candidates(
            prescription,
            clinical_evidence,
            patient_context,
            normalized_medicines,
            medicine_facts=medicine_facts,
            safety_candidates=safety_candidates,
            followup_context=followup_context,
            guideline_facts_by_section=guideline_facts_by_section,
            protocol=self.protocol,
            live_section_audit=effective.live_section_audit,
        )
        if target_sections:
            allowed = set(target_sections)
            rule_result = {
                **rule_result,
                "candidates_by_section": {
                    section: list(items or []) if section in allowed else []
                    for section, items in rule_result["candidates_by_section"].items()
                },
            }
        finish_stage("rule_candidates", started)

        started = perf_counter()
        model_candidates_by_section = _prune_candidates_for_model(
            rule_result["candidates_by_section"]
        )
        task_result = build_audit_tasks(
            prescription_id,
            prescription,
            clinical_evidence,
            patient_context,
            normalized_medicines,
            medicine_facts,
            followup_context,
            model_candidates_by_section,
            self.protocol,
            include_all_sections=effective.include_all_sections,
        )
        finish_stage("audit_tasks", started)

        started = perf_counter()
        if task_result["tasks"]:
            active_provider = (
                effective.provider
                if effective.provider is not None
                else self.provider
            )
            if active_provider is None:
                raise ValueError(
                    "A model provider is required when audit candidates need confirmation"
                )
            writer_result = write_audit_sections(
                task_result["tasks"],
                active_provider,
                max_retries=effective.max_retries,
                max_tokens=effective.max_tokens,
            )
        else:
            writer_result = {
                "section_outputs": {},
                "warnings": [],
                "model_calls": [],
            }
        finish_stage("audit_writer/model_call", started)

        started = perf_counter()
        final_report = assemble_final_report(
            prescription_id,
            prescription,
            clinical_evidence,
            patient_context,
            normalized_medicines,
            medicine_facts,
            followup_context,
            rule_result,
            writer_result,
            include_debug=effective.include_debug,
            stage_warnings=task_result.get("warnings", []),
        )
        finish_stage("final_report", started)
        total_duration_ms = round((perf_counter() - audit_started) * 1000, 3)

        if trace_callback is not None:
            trace_callback(
                _build_trace_document(
                    prescription_id,
                    prescription,
                    clinical_evidence,
                    language_context,
                    patient_context,
                    normalized_medicines,
                    planner_trace,
                    medicine_facts,
                    safety_candidates,
                    followup_context,
                    guideline_facts_by_section,
                    rule_result,
                    task_result,
                    writer_result,
                    final_report,
                    stage_durations,
                    total_duration_ms,
                    trace_full=trace_full,
                )
            )
        return final_report

    def options_for_row(self, **overrides: Any) -> AuditOptions:
        """Return per-row options while retaining cached resource settings."""
        return replace(self.options, **overrides)


def audit_prescription(
    row: Mapping[str, Any],
    options: AuditOptions | Mapping[str, Any] | None = None,
    *,
    provider: Any = None,
    trace_callback: Callable[[Mapping[str, Any]], None] | None = None,
    trace_full: bool = False,
) -> dict[str, Any]:
    """Audit one mapping through every V3 stage and return one final report."""
    orchestrator = PrescriptionAuditOrchestrator(options, provider=provider)
    return orchestrator.audit_prescription(
        row, trace_callback=trace_callback, trace_full=trace_full
    )


__all__ = [
    "AuditOptions",
    "PrescriptionAuditOrchestrator",
    "audit_prescription",
    "standardize_prescription",
]
