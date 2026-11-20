"""Build prior-visit context and provisional follow-up candidates.

The engine performs deterministic retrieval and date-window comparisons only.
Every candidate requires later confirmation and is not a final audit finding.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .advice_interpreter import (
    advice_stops_prior_medicine,
    interpret_advice_actions,
    interpret_advice_actions_with_llm,
)
from .medicine_mapper import MedicineMapper, split_medicines
from .provider import ModelProvider

_ANTIBIOTIC_RE = re.compile(
    r"\b(antibiotic|antibacterial|antimicrobial|penicillin|cephalosporin|macrolide|quinolone|fluoroquinolone)\b",
    re.IGNORECASE,
)
_ONGOING_DURATION_RE = re.compile(
    r"\b(?:continue|ongoing|until\s+further\s+(?:advice|notice))\b|চলবে|পরবর্তী\s+নির্দেশ",
    re.IGNORECASE,
)


def _empty_context(warning: str | None = None) -> dict[str, Any]:
    return {
        "prior_visits_checked": 0,
        "active_prior_medicines": [],
        "prior_current_results": [],
        "prior_ordered_not_done_tests": [],
        "current_results_matching_prior_orders": [],
        "discontinued_prior_medicines": [],
        "candidates": [],
        "warnings": [warning] if warning else [],
    }


def _text(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text.casefold() in {"", "nan", "none", "null"} else text


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    text = _text(value).replace("Z", "+00:00")
    if not text:
        raise ValueError("visit_date is missing")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for pattern in (
            "%Y-%m-%d",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%d/%m/%y %H:%M",
            "%d/%m/%Y %H:%M",
            "%d/%m/%y",
            "%d/%m/%Y",
            "%m/%d/%y %H:%M",
            "%m/%d/%Y %H:%M",
            "%m/%d/%y",
            "%m/%d/%Y",
        ):
            try:
                parsed = datetime.strptime(text, pattern)
                break
            except ValueError:
                parsed = None
        if parsed is None:
            raise ValueError(f"visit_date has unsupported format: {text!r}") from None
    return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed


def _config_value(config: Any, key: str, default: Any) -> Any:
    if isinstance(config, Mapping):
        return config.get(key, default)
    return getattr(config, key, default) if config is not None else default


def parse_duration_days(text: Any) -> int | None:
    """Parse an explicit medicine course duration; never infer a missing one."""
    value = _text(text)
    patterns = (
        r"\(\s*(\d+)\s*(day|week|month)s?\s*(?:\(s\))?\s*\)",
        r"\b(?:for\s+|x\s*)?(\d+)\s*(day|week|month)s?\b",
    )
    for pattern in patterns:
        match = re.search(pattern, value, re.IGNORECASE)
        if match:
            amount = int(match.group(1))
            multiplier = {"day": 1, "week": 7, "month": 30}[match.group(2).lower()]
            return amount * multiplier
    return None


def _is_ongoing_duration(text: Any) -> bool:
    return _ONGOING_DURATION_RE.search(_text(text)) is not None


def _canonical_test(name: Any, aliases: Mapping[str, str]) -> str:
    key = re.sub(r"\s+", " ", _text(name).casefold()).rstrip(".")
    return aliases.get(key, key)


def _split_tests(value: Any) -> list[str]:
    text = _text(value)
    if not text:
        return []
    parts: list[str] = []
    buffer: list[str] = []
    depth = 0
    for character in text:
        if character in "([{":
            depth += 1
        elif character in ")]}":
            depth = max(0, depth - 1)
        if character in ",;\n" and depth == 0:
            part = "".join(buffer).strip()
            if part:
                parts.append(part)
            buffer = []
        else:
            buffer.append(character)
    final = "".join(buffer).strip()
    if final:
        parts.append(final)
    return parts


def _parse_results(value: Any, aliases: Mapping[str, str]) -> dict[str, dict[str, str]]:
    text = _text(value)
    if not text:
        return {}
    try:
        decoded = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        decoded = None
    if isinstance(decoded, Mapping):
        return {
            _canonical_test(name, aliases): {
                "raw_name": _text(name),
                "values": _text(result),
            }
            for name, result in decoded.items()
            if _text(name)
        }

    results: dict[str, dict[str, str]] = {}
    for match in re.finditer(r"([^\[\]]+?)\s*\[([^\]]*)\]", text):
        raw_name = match.group(1).strip().lstrip(",;").strip()
        if raw_name:
            results[_canonical_test(raw_name, aliases)] = {
                "raw_name": raw_name,
                "values": match.group(2).strip(),
            }
    if results:
        return results
    for part in _split_tests(text):
        match = re.match(r"^([^:=]+)\s*[:=]\s*(.+)$", part)
        if match:
            raw_name = match.group(1).strip()
            results[_canonical_test(raw_name, aliases)] = {
                "raw_name": raw_name,
                "values": match.group(2).strip(),
            }
    return results


def _row_value(row: Mapping[str, Any], *names: str) -> Any:
    lowered = {str(key).casefold(): value for key, value in row.items()}
    for name in names:
        if name.casefold() in lowered:
            return lowered[name.casefold()]
    return ""


def _prior_medicine_entries(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    encoded = _text(_row_value(row, "medications_json"))
    if encoded:
        try:
            decoded = json.loads(encoded)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, list):
            entries = []
            for item in decoded:
                if isinstance(item, Mapping):
                    raw = _text(item.get("raw") or item.get("raw_text") or item.get("name"))
                    duration = item.get("duration_days")
                    try:
                        duration_days = int(duration) if duration is not None else parse_duration_days(raw)
                    except (TypeError, ValueError):
                        duration_days = parse_duration_days(raw)
                    entries.append({**dict(item), "raw_text": raw, "duration_days": duration_days})
                elif _text(item):
                    raw = _text(item)
                    entries.append({"raw_text": raw, "duration_days": parse_duration_days(raw)})
            return entries

    medication_text = _row_value(row, "medication", "medications", "medication & dosing", "medication_text")
    return [{"raw_text": raw, "duration_days": parse_duration_days(raw)} for raw in split_medicines(medication_text)]


def _fact_index(fact_results: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, str]]]:
    result: dict[str, list[dict[str, str]]] = {}
    for item in fact_results or []:
        generic_id = _text(item.get("generic_id", ""))
        if not generic_id:
            continue
        facts = []
        for fact in item.get("facts", []) or []:
            if not isinstance(fact, Mapping):
                continue
            text = _text(fact.get("text", ""))
            fact_type = _text(fact.get("fact_type", ""))
            if text and fact_type:
                facts.append(
                    {
                        "fact_type": fact_type,
                        "text": text,
                        "source_field": _text(fact.get("source_field", "")),
                    }
                )
        if facts:
            result.setdefault(generic_id, []).extend(facts)
    return result


def _contains_name(text: str, name: str) -> bool:
    clean = re.sub(r"[^a-z0-9\s]+", " ", _text(name).casefold())
    hay = re.sub(r"[^a-z0-9\s]+", " ", _text(text).casefold())
    terms = [clean]
    terms.extend(token for token in clean.split() if len(token) >= 5)
    return any(term and re.search(rf"(?<!\w){re.escape(term)}(?!\w)", hay) for term in terms)


def _drug_class_text(medicine: Mapping[str, Any], facts_by_id: Mapping[str, Sequence[Mapping[str, str]]]) -> str:
    generic_id = _text(medicine.get("generic_id", ""))
    return " ".join(fact["text"] for fact in facts_by_id.get(generic_id, []) if fact.get("fact_type") == "drug_class")


def _class_tokens(text: str) -> set[str]:
    stop = {"drug", "class", "medicine", "medicines", "therapeutic", "agent", "agents"}
    return {token for token in re.findall(r"[a-z][a-z0-9-]{4,}", _text(text).casefold()) if token not in stop}


def _is_antibiotic(medicine: Mapping[str, Any], facts_by_id: Mapping[str, Sequence[Mapping[str, str]]]) -> bool:
    text = " ".join(
        [
            _text(medicine.get("raw_text", "")),
            _text(medicine.get("generic_name", "")),
            _drug_class_text(medicine, facts_by_id),
        ]
    )
    return _ANTIBIOTIC_RE.search(text) is not None


def _followup_candidate(
    section: str,
    rule_name: str,
    why: str,
    evidence: str,
    **extras: Any,
) -> dict[str, Any]:
    return {
        "section": section,
        "rule_name": rule_name,
        "trigger_source": "followup_context_engine",
        "why_candidate": why,
        "evidence": evidence,
        "needs_llm_confirmation": True,
        "severity": "needs_review",
        **extras,
    }


def _query_prior_visits(
    db_path: Path,
    patient_id: str,
    visit_dt: datetime,
    lookback_days: int,
) -> list[dict[str, Any]]:
    with closing(sqlite3.connect(db_path)) as connection, connection:
        connection.row_factory = sqlite3.Row
        table = connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='visits'").fetchone()
        if table is None:
            raise ValueError("SQLite database has no visits table")
        columns = [row[1] for row in connection.execute("PRAGMA table_info(visits)")]
        lower_columns = {column.casefold(): column for column in columns}
        patient_column = lower_columns.get("patient_id")
        date_column = lower_columns.get("visit_datetime") or lower_columns.get("visit_date")
        if not patient_column or not date_column:
            raise ValueError("visits table requires patient_id and visit_datetime/visit_date")
        earliest = visit_dt - timedelta(days=lookback_days)
        query = (
            f'SELECT * FROM visits WHERE CAST("{patient_column}" AS TEXT) = ? '
            f'AND "{date_column}" >= ? AND "{date_column}" < ? '
            f'ORDER BY "{date_column}" DESC'
        )
        rows = connection.execute(
            query,
            (patient_id, earliest.isoformat(sep=" "), visit_dt.isoformat(sep=" ")),
        ).fetchall()
        return [dict(row) for row in rows]


def _monitoring_exempt(
    canonical_test: str,
    today_prescription: Mapping[str, Any],
    exemptions: Any,
    aliases: Mapping[str, str],
) -> bool:
    diagnosis = " ".join(
        (
            _text(_row_value(today_prescription, "Diagnosis")),
            _text(_row_value(today_prescription, "Chief_Complaint", "Chief Complaint", "Chief Complain")),
        )
    ).casefold()
    if isinstance(exemptions, Mapping):
        for condition, tests in exemptions.items():
            if str(condition).casefold() in diagnosis:
                values = tests if isinstance(tests, Sequence) and not isinstance(tests, str) else [tests]
                if canonical_test in {_canonical_test(test, aliases) for test in values}:
                    return True
    elif isinstance(exemptions, Sequence) and not isinstance(exemptions, str):
        for rule in exemptions:
            if not isinstance(rule, Mapping):
                continue
            keywords = rule.get("diagnosis_keywords", rule.get("conditions", []))
            tests = rule.get("investigations", rule.get("tests", []))
            if isinstance(keywords, str):
                keywords = [keywords]
            if isinstance(tests, str):
                tests = [tests]
            if any(str(keyword).casefold() in diagnosis for keyword in keywords):
                if canonical_test in {_canonical_test(test, aliases) for test in tests}:
                    return True
    return False


def build_followup_context(
    patient_id: Any,
    visit_date: Any,
    today_prescription: Mapping[str, Any],
    today_medicines: Sequence[Mapping[str, Any]],
    *,
    db_path: str | Path | None = None,
    lookback_days: int = 30,
    buffer_days: int = 0,
    medicine_mapper: MedicineMapper | None = None,
    investigation_validity: Mapping[str, int] | None = None,
    investigation_validity_default: int = 0,
    investigation_aliases: Mapping[str, str] | None = None,
    monitoring_exemptions: Any = None,
    today_medicine_facts: Sequence[Mapping[str, Any]] | None = None,
    medicine_fact_lookup: Callable[[Mapping[str, Any]], Sequence[Mapping[str, Any]]] | None = None,
    advice_action_provider: ModelProvider | None = None,
    protocol: Any = None,
) -> dict[str, Any]:
    """Retrieve prior visits and create provisional medicine/test candidates."""
    patient_key = _text(patient_id)
    if not patient_key:
        return _empty_context("patient_id is missing; follow-up context was not retrieved")
    if not _text(visit_date):
        return _empty_context("visit_date is missing; follow-up context was not retrieved")
    if db_path is None or not _text(db_path):
        return _empty_context("database path is missing; follow-up context was not retrieved")
    database = Path(db_path)
    if not database.exists():
        return _empty_context(f"database path does not exist: {database}")
    try:
        today_dt = _as_datetime(visit_date)
    except ValueError as exc:
        return _empty_context(str(exc))

    validity_source = investigation_validity
    if validity_source is None:
        validity_source = _config_value(protocol, "investigation_validity", {})
    default_validity = investigation_validity_default
    if investigation_validity_default == 0:
        default_validity = int(_config_value(protocol, "investigation_validity_default", 0) or 0)
    alias_source = investigation_aliases
    if alias_source is None:
        alias_source = _config_value(protocol, "investigation_aliases", {})
    exemption_source = monitoring_exemptions
    if exemption_source is None:
        exemption_source = _config_value(
            protocol,
            "monitoring_exemptions",
            _config_value(protocol, "investigation_monitoring_exemptions", {}),
        )
    aliases = {
        re.sub(r"\s+", " ", str(key).strip().casefold()).rstrip("."): re.sub(
            r"\s+", " ", str(value).strip().casefold()
        ).rstrip(".")
        for key, value in dict(alias_source or {}).items()
    }
    validity = {_canonical_test(key, aliases): int(value) for key, value in dict(validity_source or {}).items()}

    context = _empty_context()
    context["current_patient_id"] = patient_key
    context["current_visit_date"] = today_dt.isoformat(sep=" ")
    try:
        visits = _query_prior_visits(database, patient_key, today_dt, lookback_days)
    except (sqlite3.Error, ValueError) as exc:
        context["warnings"].append(f"follow-up database query failed: {exc}")
        return context
    context["prior_visits_checked"] = len(visits)
    today_advice = _row_value(today_prescription, "Advice", "advice")
    advice_actions = interpret_advice_actions(today_advice)
    if not advice_actions and _text(today_advice) and advice_action_provider is not None:
        try:
            advice_actions = interpret_advice_actions_with_llm(
                today_advice,
                advice_action_provider,
            )
        except Exception as exc:  # pragma: no cover - defensive; provider errors are surfaced in trace.
            context["warnings"].append(f"advice action classifier failed: {type(exc).__name__}")
    if advice_actions:
        context["advice_actions"] = advice_actions

    today_generic_ids = {
        _text(item.get("generic_id", "")) for item in today_medicines if _text(item.get("generic_id", ""))
    }
    today_tests = {
        _canonical_test(test, aliases)
        for test in _split_tests(_row_value(today_prescription, "Investigation", "Investigations"))
    }
    today_results = _parse_results(
        _row_value(today_prescription, "Investigation_Results", "Investigation Results", "investigation_result"),
        aliases,
    )
    facts_by_id = _fact_index(today_medicine_facts or [])

    for visit in visits:
        prior_raw_date = _text(_row_value(visit, "visit_datetime", "visit_date"))
        try:
            prior_dt = _as_datetime(prior_raw_date)
        except ValueError:
            context["warnings"].append(f"prior visit has invalid date and was skipped: {prior_raw_date!r}")
            continue
        prior_date = prior_dt.date()
        age_days = (today_dt.date() - prior_date).days

        for prior_entry in _prior_medicine_entries(visit):
            raw = _text(prior_entry.get("raw_text", ""))
            duration_days = prior_entry.get("duration_days")
            ongoing = duration_days is None and _is_ongoing_duration(raw)
            if duration_days is None and not ongoing:
                context["warnings"].append(
                    f"prior medicine duration is unknown; active status was not assumed: {raw!r} "
                    f"(visit {prior_raw_date})"
                )
                continue
            course_end = None if ongoing else prior_date + timedelta(days=int(duration_days) + buffer_days)
            if course_end is not None and today_dt.date() > course_end:
                continue
            if _text(prior_entry.get("generic_id", "")):
                mapped = dict(prior_entry)
            elif medicine_mapper is not None:
                mapped = medicine_mapper.map_one(raw)
            else:
                context["warnings"].append(f"prior medicine could not be resolved without a medicine_mapper: {raw!r}")
                mapped = {}
            active = {
                "prior_visit_date": prior_raw_date,
                "raw_text": raw,
                "generic_id": _text(mapped.get("generic_id", "")),
                "generic_name": _text(mapped.get("generic_name", "")),
                "duration_days": None if ongoing else int(duration_days),
                "course_end": "ongoing" if ongoing else course_end.isoformat(),
                "ongoing": ongoing,
                "evidence": raw,
            }
            if advice_stops_prior_medicine(today_advice, active, actions=advice_actions):
                context["discontinued_prior_medicines"].append(
                    {
                        **active,
                        "discontinuation_evidence": _text(today_advice),
                        "reason": "current advice indicates prior/old medicine should not be continued",
                    }
                )
                continue
            context["active_prior_medicines"].append(active)
            if active["generic_id"] and medicine_fact_lookup is not None:
                for fact_result in medicine_fact_lookup(active) or []:
                    for generic_id, facts in _fact_index([fact_result]).items():
                        facts_by_id.setdefault(generic_id, []).extend(facts)
            if active["generic_id"] and active["generic_id"] in today_generic_ids:
                current_same = next(
                    (item for item in today_medicines if _text(item.get("generic_id", "")) == active["generic_id"]),
                    {},
                )
                evidence = (
                    f"Prior visit {prior_raw_date}: {raw}; course_end={active['course_end']} | "
                    f"Current medicine: {_text(current_same.get('raw_text', ''))}"
                )
                context["candidates"].append(
                    _followup_candidate(
                        "Rx",
                        "duplicate_active_prior_medicine_candidate",
                        "A prior medicine course is still active and today's prescription repeats the same generic.",
                        evidence,
                        prior_visit_date=prior_raw_date,
                        generic_id=active["generic_id"],
                        generic_name=active["generic_name"],
                        prior_medicine=raw,
                        current_medicine=_text(current_same.get("raw_text", "")),
                        course_end=active["course_end"],
                    )
                )

        for active in list(context["active_prior_medicines"]):
            prior_id = _text(active.get("generic_id", ""))
            prior_name = _text(active.get("generic_name", ""))
            if not prior_id:
                continue
            prior_as_medicine = {
                "raw_text": active.get("raw_text", ""),
                "generic_id": prior_id,
                "generic_name": prior_name,
            }
            for current in today_medicines:
                current_id = _text(current.get("generic_id", ""))
                current_name = _text(current.get("generic_name", ""))
                if not current_id or current_id == prior_id:
                    continue
                current_facts = facts_by_id.get(current_id, [])
                prior_facts = list(facts_by_id.get(prior_id, []))
                interaction_checks = [(fact, prior_name) for fact in current_facts] + [
                    (fact, current_name) for fact in prior_facts
                ]
                for fact, other_name in interaction_checks:
                    if fact.get("fact_type") != "interaction":
                        continue
                    fact_text = fact.get("text", "")
                    if _contains_name(fact_text, other_name):
                        evidence = (
                            f"Prior visit {active.get('prior_visit_date')}: {active.get('raw_text')} "
                            f"(course_end={active.get('course_end')}) | "
                            f"Current medicine: {_text(current.get('raw_text', ''))} | "
                            f"Medicine fact: {fact_text}"
                        )
                        context["candidates"].append(
                            _followup_candidate(
                                "Rx",
                                "ddi_with_active_prior_medicine_candidate",
                                "A current medicine has RAG interaction evidence involving a still-active prior medicine.",
                                evidence,
                                prior_visit_date=active.get("prior_visit_date"),
                                prior_generic_id=prior_id,
                                prior_generic_name=prior_name,
                                current_generic_id=current_id,
                                current_generic_name=current_name,
                                course_end=active.get("course_end"),
                                source_field=fact.get("source_field", ""),
                                rag_supported=True,
                                evidence_strength="high",
                                must_review_if_rejected=True,
                            )
                        )
                prior_class = _drug_class_text(prior_as_medicine, facts_by_id)
                current_class = _drug_class_text(current, facts_by_id)
                overlap = _class_tokens(prior_class) & _class_tokens(current_class)
                if overlap:
                    evidence = (
                        f"Prior visit {active.get('prior_visit_date')}: {active.get('raw_text')} "
                        f"(course_end={active.get('course_end')}) | "
                        f"Current medicine: {_text(current.get('raw_text', ''))} | "
                        f"Shared class evidence: {', '.join(sorted(overlap))}"
                    )
                    context["candidates"].append(
                        _followup_candidate(
                            "Rx",
                            "therapeutic_duplication_with_prior_candidate",
                            "A still-active prior medicine and today's medicine have overlapping therapeutic class evidence.",
                            evidence,
                            prior_visit_date=active.get("prior_visit_date"),
                            prior_generic_id=prior_id,
                            prior_generic_name=prior_name,
                            current_generic_id=current_id,
                            current_generic_name=current_name,
                            duplicate_class=", ".join(sorted(overlap)),
                            course_end=active.get("course_end"),
                        )
                    )
                if _is_antibiotic(prior_as_medicine, facts_by_id) and _is_antibiotic(current, facts_by_id):
                    evidence = (
                        f"Prior active antibiotic from {active.get('prior_visit_date')}: {active.get('raw_text')} "
                        f"(course_end={active.get('course_end')}) | "
                        f"Current antibiotic: {_text(current.get('raw_text', ''))}"
                    )
                    context["candidates"].append(
                        _followup_candidate(
                            "Rx",
                            "active_antibiotic_overlap_or_switch_candidate",
                            "A prior antibiotic course may still be active while today's prescription adds or switches antibiotic therapy.",
                            evidence,
                            prior_visit_date=active.get("prior_visit_date"),
                            prior_generic_id=prior_id,
                            prior_generic_name=prior_name,
                            current_generic_id=current_id,
                            current_generic_name=current_name,
                            course_end=active.get("course_end"),
                        )
                    )

        ordered_text = _row_value(visit, "investigation", "investigations")
        result_text = _row_value(visit, "investigation_result", "investigation results", "investigation_results")
        ordered = _split_tests(ordered_text)
        results = _parse_results(result_text, aliases)
        for raw_test in ordered:
            canonical = _canonical_test(raw_test, aliases)
            result = results.get(canonical)
            if result is None:
                today_result = today_results.get(canonical)
                if today_result is not None:
                    context["current_results_matching_prior_orders"].append(
                        {
                            "investigation": raw_test,
                            "canonical": canonical,
                            "prior_visit_date": prior_raw_date,
                            "current_result": today_result["values"],
                            "evidence": today_result["values"],
                        }
                    )
                    context["candidates"].append(
                        _followup_candidate(
                            "Inv",
                            "prior_ordered_test_result_followup_candidate",
                            "A test ordered in a prior visit now has a current result that may need follow-up interpretation.",
                            (
                                f"Prior visit {prior_raw_date} ordered: {raw_test} | "
                                f"Current result: {today_result['values']}"
                            ),
                            prior_visit_date=prior_raw_date,
                            investigation=raw_test,
                            canonical=canonical,
                            current_result=today_result["values"],
                        )
                    )
                    continue
                context["prior_ordered_not_done_tests"].append(
                    {
                        "investigation": raw_test,
                        "canonical": canonical,
                        "prior_visit_date": prior_raw_date,
                        "evidence": raw_test,
                    }
                )
                continue
            validity_days = validity.get(canonical, default_validity)
            if validity_days <= 0 or age_days > validity_days:
                continue
            current_result = {
                "investigation": raw_test,
                "canonical": canonical,
                "result": result["values"],
                "prior_visit_date": prior_raw_date,
                "days_since": age_days,
                "validity_days": validity_days,
                "evidence": _text(result_text),
            }
            context["prior_current_results"].append(current_result)
            if canonical in today_tests and not _monitoring_exempt(
                canonical, today_prescription, exemption_source, aliases
            ):
                valid_until = (prior_date + timedelta(days=validity_days)).isoformat()
                context["candidates"].append(
                    _followup_candidate(
                        "Inv",
                        "redundant_investigation_candidate",
                        "A same investigation is ordered today while a recent prior result remains within its configured validity window.",
                        (
                            f"Prior visit {prior_raw_date}: {raw_test} result {result['values']} "
                            f"valid_until={valid_until} | Current ordered test: {raw_test}"
                        ),
                        prior_visit_date=prior_raw_date,
                        investigation=raw_test,
                        canonical=canonical,
                        valid_until=valid_until,
                        prior_result=result["values"],
                    )
                )

    seen_candidates: set[tuple[str, str, str]] = set()
    deduped_candidates: list[dict[str, Any]] = []
    for candidate in context["candidates"]:
        key = (
            _text(candidate.get("section", "")),
            _text(candidate.get("rule_name", "")),
            _text(candidate.get("evidence", "")),
        )
        if key in seen_candidates:
            continue
        seen_candidates.add(key)
        deduped_candidates.append(candidate)
    context["candidates"] = deduped_candidates
    return context


__all__ = ["build_followup_context", "parse_duration_days"]
