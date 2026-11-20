"""Retrieve section-scoped medicine evidence by exact generic ID.

This module cleans and selects source facts only. It does not decide whether a
medicine creates a dose, interaction, pregnancy, or other clinical error.
"""

from __future__ import annotations

import csv
import html
import json
import re
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any

SECTION_FIELDS: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "Rx": (
        ("drug_class", ("drug class",)),
        ("indication", ("indication", "indication description")),
        ("dosage", ("dosage description",)),
        ("administration", ("administration description",)),
        ("interaction", ("interaction description",)),
        ("contraindication", ("contraindications description",)),
        ("precaution", ("precautions description",)),
        ("pregnancy_lactation", ("pregnancy and lactation description",)),
        ("pediatric_usage", ("pediatric usage description",)),
    ),
    "Adv": (
        ("administration", ("administration description",)),
        ("reconstitution", ("reconstitution description",)),
        ("storage", ("storage conditions description",)),
        ("precaution", ("precautions description",)),
        ("pregnancy_lactation", ("pregnancy and lactation description",)),
        ("side_effects", ("side effects description",)),
    ),
    "Dx": (
        ("drug_class", ("drug class",)),
        ("indication", ("indication", "indication description")),
    ),
}


FACT_TYPE_ALIASES = {
    "administration_rule": "administration",
    "dose_rule": "dosage",
    "duration_rule": "dosage",
    "drug_interaction": "interaction",
    "pediatric_rule": "pediatric_usage",
    "side_effect": "side_effects",
    "therapeutic_class": "drug_class",
    "pregnancy": "pregnancy_lactation",
    "lactation": "pregnancy_lactation",
    "women_of_childbearing_potential": "pregnancy_lactation",
    "fertility": "pregnancy_lactation",
}


def canonical_fact_type(value: Any) -> str:
    fact_type = str(value or "").strip()
    return FACT_TYPE_ALIASES.get(fact_type.casefold(), fact_type)


def clean_monograph_text(value: Any) -> str:
    """Remove HTML while retaining readable separation between elements."""
    text = str(value or "")
    text = re.sub(r"(?i)<\s*(?:br|/p|/div|/li)\s*/?>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _bounded(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 1:
        return "…"[:limit]
    return text[: limit - 1].rstrip() + "…"


def _canonical_section(section: str) -> str:
    lookup = {name.casefold(): name for name in SECTION_FIELDS}
    canonical = lookup.get(str(section).strip().casefold())
    if not canonical:
        raise ValueError(f"Unsupported medicine-fact section: {section!r}")
    return canonical


class MedicineFactStore:
    """CSV-backed fact retrieval with an optional annotated JSONL overlay."""

    def __init__(
        self,
        generic_csv: str | Path | None = None,
        annotated_facts_path: str | Path | None = None,
        *,
        max_text_length: int = 1200,
    ) -> None:
        if max_text_length < 1:
            raise ValueError("max_text_length must be positive")
        package_dir = Path(__file__).resolve().parent
        data_candidates = (package_dir / "data", package_dir.parent / "data")
        data_dir = next(
            (candidate for candidate in data_candidates if (candidate / "generic.csv").is_file()),
            data_candidates[0],
        )
        default_generic_csv = data_dir / "generic.csv"
        default_annotated = data_dir / "rag" / "drug_extracted_facts_merged.jsonl"
        self.generic_csv = Path(generic_csv) if generic_csv else default_generic_csv
        use_default_annotated = False
        try:
            use_default_annotated = self.generic_csv.resolve() == default_generic_csv.resolve()
        except FileNotFoundError:
            use_default_annotated = False
        if annotated_facts_path:
            self.annotated_facts_path = Path(annotated_facts_path)
        elif use_default_annotated and default_annotated.is_file():
            self.annotated_facts_path = default_annotated
        else:
            self.annotated_facts_path = None
        self.max_text_length = max_text_length
        self._generic_rows = self._load_generic_rows(self.generic_csv)
        self._annotated = self._load_annotated(self.annotated_facts_path)

    @staticmethod
    def _load_generic_rows(path: Path) -> dict[str, dict[str, str]]:
        rows: dict[str, dict[str, str]] = {}
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for source in csv.DictReader(handle):
                normalized = {
                    str(key or "").strip().casefold(): str(value or "").strip() for key, value in source.items()
                }
                generic_id = normalized.get("generic id", "")
                if generic_id:
                    rows[generic_id] = normalized
        return rows

    @staticmethod
    def _load_annotated(
        path: Path | None,
    ) -> dict[str, list[dict[str, str]]]:
        indexed: dict[str, list[dict[str, str]]] = defaultdict(list)
        if path is None:
            return indexed
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid annotated fact JSONL at line {line_number}: {exc.msg}") from exc
                generic_id = str(record.get("generic_id", "")).strip()
                if not generic_id:
                    continue
                generic_name = str(record.get("generic_name", "")).strip()
                facts = record.get("facts")
                if not isinstance(facts, list):
                    facts = [record]
                for fact in facts:
                    if not isinstance(fact, Mapping):
                        continue
                    original_fact_type = str(fact.get("fact_type", "")).strip()
                    fact_type = canonical_fact_type(fact.get("canonical_fact_type") or original_fact_type)
                    text = clean_monograph_text(fact.get("text") or fact.get("evidence") or "")
                    if not fact_type or not text:
                        continue
                    section = str(record.get("section") or fact.get("section") or "").strip()
                    indexed[generic_id].append(
                        {
                            "generic_name": generic_name,
                            "section": section,
                            "fact_type": fact_type,
                            "source_fact_type": original_fact_type or fact_type,
                            "text": text,
                            "source_field": str(fact.get("source_field", "annotated fact")).strip(),
                            "source_quality": str(fact.get("source_quality", "")).strip(),
                            "validation_status": str(fact.get("validation_status", "")).strip(),
                            "confidence": str(fact.get("confidence", "")).strip(),
                            "source_folder": str(fact.get("source_folder", "")).strip(),
                            "source_file": str(fact.get("source_file", "")).strip(),
                            "source_line": str(fact.get("source_line", "")).strip(),
                            "source_task_id": str(fact.get("source_task_id", "")).strip(),
                        }
                    )
        return indexed

    def _annotated_for(self, generic_id: str, section: str) -> list[dict[str, str]]:
        allowed = {fact_type for fact_type, _ in SECTION_FIELDS[section]}
        facts = []
        for source in self._annotated.get(generic_id, []):
            fact_section = source["section"].casefold()
            if fact_section and fact_section != section.casefold():
                continue
            if source["fact_type"] not in allowed:
                continue
            facts.append(
                {
                    "fact_type": source["fact_type"],
                    "text": _bounded(source["text"], self.max_text_length),
                    "source_type": "annotated_fact",
                    "source_field": source["source_field"],
                    "source_fact_type": source.get("source_fact_type", source["fact_type"]),
                    "source_quality": source.get("source_quality", ""),
                    "validation_status": source.get("validation_status", ""),
                    "confidence": source.get("confidence", ""),
                    "source_folder": source.get("source_folder", ""),
                    "source_file": source.get("source_file", ""),
                    "source_line": source.get("source_line", ""),
                    "source_task_id": source.get("source_task_id", ""),
                }
            )
        return facts

    def get_facts(
        self,
        medicine: Mapping[str, Any] | str | None,
        section: str,
    ) -> dict[str, Any]:
        """Return evidence for one mapped medicine and one audit section."""
        canonical_section = _canonical_section(section)
        if isinstance(medicine, Mapping):
            generic_id = str(medicine.get("generic_id", "") or "").strip()
            supplied_name = str(medicine.get("generic_name", "") or "").strip()
            known_supportive_item = bool(medicine.get("known_supportive_item", False))
        else:
            generic_id = str(medicine or "").strip()
            supplied_name = ""
            known_supportive_item = False

        result: dict[str, Any] = {
            "generic_id": generic_id,
            "generic_name": supplied_name,
            "section": canonical_section,
            "facts": [],
            "warnings": [],
        }
        if not generic_id:
            if known_supportive_item:
                return result
            result["warnings"].append("missing generic_id; no medicine facts retrieved")
            return result

        row = self._generic_rows.get(generic_id)
        annotated = self._annotated_for(generic_id, canonical_section)
        annotated_name = next(
            (item["generic_name"] for item in self._annotated.get(generic_id, []) if item["generic_name"]),
            "",
        )
        if row:
            result["generic_name"] = row.get("generic name", "") or supplied_name
        elif annotated_name:
            result["generic_name"] = annotated_name

        if not row and not annotated:
            result["warnings"].append(f"generic_id {generic_id!r} not found in configured fact sources")
            return result

        result["facts"].extend(annotated)
        annotated_types = {fact["fact_type"] for fact in annotated}
        if row:
            for fact_type, source_fields in SECTION_FIELDS[canonical_section]:
                if fact_type in annotated_types:
                    continue
                for source_field in source_fields:
                    text = clean_monograph_text(row.get(source_field, ""))
                    if not text:
                        continue
                    result["facts"].append(
                        {
                            "fact_type": fact_type,
                            "text": _bounded(text, self.max_text_length),
                            "source_type": "generic_csv",
                            "source_field": source_field,
                        }
                    )
                    break
        return result


def get_medicine_facts(
    medicine: Mapping[str, Any] | str | None,
    section: str,
    generic_csv: str | Path | None = None,
    annotated_facts_path: str | Path | None = None,
    *,
    max_text_length: int = 1200,
) -> dict[str, Any]:
    """Convenience function for a single retrieval."""
    return MedicineFactStore(
        generic_csv,
        annotated_facts_path,
        max_text_length=max_text_length,
    ).get_facts(medicine, section)


__all__ = [
    "MedicineFactStore",
    "FACT_TYPE_ALIASES",
    "SECTION_FIELDS",
    "canonical_fact_type",
    "clean_monograph_text",
    "get_medicine_facts",
]
