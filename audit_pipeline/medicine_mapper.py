"""Deterministic medicine text parsing and brand-to-generic mapping.

The mapper uses exact normalized names from ``medicine.csv`` and
``generic.csv``. It intentionally performs no fuzzy drug-name matching.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

from .frequency_parser import parse_frequency


_EMPTY = {"", "nan", "none", "null"}
_SALT_WORDS = {
    "anhydrous",
    "besilate",
    "cilexetil",
    "dihydrate",
    "fumarate",
    "hemifumarate",
    "hydrate",
    "hydrochloride",
    "maleate",
    "medoxomil",
    "monohydrate",
    "potassium",
    "sodium",
    "sulphate",
    "sulfate",
    "trihydrate",
}
_FORM_TERMS = (
    "powder for suspension",
    "capsule (delayed release)",
    "capsule (timed release)",
    "pediatric drops",
    "oral suspension",
    "iv infusion",
    "iv injection",
    "suppository",
    "suspension",
    "injection",
    "capsule",
    "tablet",
    "syrup",
    "cream",
    "ointment",
    "drops",
    "drop",
    "gel",
    "solution",
)
_STRENGTH = re.compile(
    r"\d+(?:\.\d+)?\s*(?:mcg|mg|gm|g|ml|iu|%)"
    r"(?:\s*/\s*\d*(?:\.\d+)?\s*(?:ml|vial|dose))?"
    r"(?:\s*\+\s*\d+(?:\.\d+)?\s*(?:mcg|mg|gm|g|ml|iu|%))?",
    re.IGNORECASE,
)
_MG_STRENGTH = re.compile(r"(\d+(?:\.\d+)?)\s*mg\b", re.IGNORECASE)
_STRENGTH_VALUE_UNIT = re.compile(
    r"(\d+(?:\.\d+)?)\s*(mcg|mg|gm|g|ml|iu|%)\b", re.IGNORECASE
)
_FREQUENCY_PARENS = re.compile(r"\(([^()]*)\)")
_INSTRUCTION_BRACKETS = re.compile(r"\[([^\[\]]+)\]\s*$")
_INSTRUCTION_JSON = re.compile(
    r'"(?:additionalInstructions|instructions)"\s*:\s*"([^"]*)"',
    re.IGNORECASE,
)
_BANGLA_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _key(value: Any) -> str:
    """Case-insensitive exact key; spaces and hyphens are equivalent."""
    text = (
        _clean(value)
        .casefold()
        .replace("\\'", "'")
        .replace("\\’", "'")
        .replace("’", "'")
    )
    return re.sub(r"[\s-]+", " ", text).strip()


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", _clean(value).casefold())


def _written_instruction(value: Any) -> str:
    """Extract the writer's medication instruction without translating it."""
    text = str(value or "")
    match = _INSTRUCTION_JSON.search(text) or _INSTRUCTION_BRACKETS.search(text)
    return _clean(match.group(1)) if match else ""


def split_medicines(text: Any) -> list[str]:
    """Split commas/semicolons/newlines only when outside brackets."""
    value = str(text or "").strip()
    if value.casefold() in _EMPTY:
        return []
    parts: list[str] = []
    buffer: list[str] = []
    depth = 0
    pairs = {"(": ")", "[": "]", "{": "}"}
    closers = set(pairs.values())
    for character in value:
        if character in pairs:
            depth += 1
        elif character in closers:
            depth = max(0, depth - 1)
        if (character in ",;\n") and depth == 0:
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


def _before_dose_parentheses(raw: str) -> str:
    return raw.split("(", 1)[0].strip().rstrip("-").strip()


def _written_strength(raw: str) -> str:
    match = _STRENGTH.search(_before_dose_parentheses(raw))
    return match.group(0).strip() if match else ""


def _strength_value_unit(raw: str, fallback_strength: str = "") -> tuple[float | None, str]:
    text = _written_strength(raw) or fallback_strength
    match = _STRENGTH_VALUE_UNIT.search(text)
    if not match:
        return None, ""
    return float(match.group(1)), match.group(2).casefold()


def _strength_mg(raw: str, fallback_strength: str = "") -> float | None:
    text = f"{_before_dose_parentheses(raw)} {fallback_strength}"
    match = _MG_STRENGTH.search(text)
    return float(match.group(1)) if match else None


def _mg_from_strength_text(value: Any) -> float | None:
    match = _MG_STRENGTH.search(str(value or ""))
    return float(match.group(1)) if match else None


def _component_names(generic_name: Any) -> list[str]:
    return [_clean(part) for part in re.split(r"\s*\+\s*", _clean(generic_name)) if _clean(part)]


def _component_strengths(generic_name: Any, strength: Any) -> list[dict[str, Any]]:
    components = _component_names(generic_name)
    strength_parts = [_clean(part) for part in re.split(r"\s*\+\s*", _clean(strength)) if _clean(part)]
    result: list[dict[str, Any]] = []
    for index, component in enumerate(components):
        strength_part = strength_parts[index] if index < len(strength_parts) else ""
        result.append(
            {
                "component_name": component,
                "strength_text": strength_part,
                "strength_mg": _mg_from_strength_text(strength_part),
                "is_primary": index == 0,
            }
        )
    return result


def _liquid_daily_dose_mg(
    raw: str,
    fallback_strength: str,
    frequency: dict[str, Any],
    *,
    spoon_ml: float | None = None,
) -> tuple[float | None, str]:
    strength_text = _written_strength(raw) or fallback_strength
    match = re.search(
        r"(\d+(?:\.\d+)?)\s*mg\s*/\s*(\d+(?:\.\d+)?)\s*ml\b",
        strength_text,
        re.IGNORECASE,
    )
    if not match or frequency.get("frequency_type") not in {"liquid_spoon_daily", "liquid_ml_daily"}:
        return None, ""
    amount = frequency.get("amount_per_dose")
    times = frequency.get("times_per_day")
    unit = frequency.get("amount_unit")
    if amount is None or times is None:
        return None, "liquid frequency missing amount or times per day"
    if unit == "ml":
        ml_per_dose = float(amount)
    elif unit == "spoon" and spoon_ml is not None:
        ml_per_dose = float(amount) * spoon_ml
    else:
        return None, "spoon volume is not configured; mg/day not calculated"
    mg_per_ml = float(match.group(1)) / float(match.group(2))
    return round(mg_per_ml * ml_per_dose * float(times), 3), ""


def _dose_calculation(
    raw: str,
    fallback_strength: str = "",
    *,
    spoon_ml: float | None = None,
) -> dict[str, Any]:
    strength_mg = _strength_mg(raw, fallback_strength)
    written_strength = _written_strength(raw)
    written_strength_mg = _mg_from_strength_text(written_strength)
    reference_strength_mg = _mg_from_strength_text(fallback_strength)
    strength_text = _written_strength(raw) or fallback_strength
    prefer_liquid = bool(re.search(r"\d+(?:\.\d+)?\s*mg\s*/\s*\d+(?:\.\d+)?\s*ml\b", strength_text, re.IGNORECASE))
    frequency = parse_frequency(raw, prefer_liquid=prefer_liquid)
    units_per_day = frequency.get("units_per_day")
    daily = (
        round(strength_mg * units_per_day, 3)
        if strength_mg is not None
        and units_per_day is not None
        and frequency.get("frequency_type") == "fixed_solid_daily"
        else None
    )
    reason = frequency.get("reason", "")
    liquid_daily, liquid_reason = _liquid_daily_dose_mg(
        raw, fallback_strength, frequency, spoon_ml=spoon_ml
    )
    if daily is None and liquid_daily is not None:
        daily = liquid_daily
        reason = "liquid mg/day calculated from mg/ml strength"
    elif daily is None and liquid_reason:
        reason = liquid_reason
    return {
        "strength_mg": strength_mg,
        "written_strength": written_strength,
        "written_strength_mg": written_strength_mg,
        "reference_strength": fallback_strength,
        "reference_strength_mg": reference_strength_mg,
        "units_per_day": units_per_day,
        "administrations_per_day": frequency.get("administrations_per_day"),
        "max_units_per_administration": frequency.get("max_units_per_administration"),
        "interval_pattern": frequency.get("interval_pattern", ""),
        "calculated_daily_dose_mg": daily,
        "raw_frequency": frequency.get("raw_frequency", ""),
        "normalized_frequency": frequency.get("normalized_frequency", ""),
        "frequency_type": frequency.get("frequency_type", "unknown"),
        "schedule_parts": frequency.get("schedule_parts", []),
        "amount_per_dose": frequency.get("amount_per_dose"),
        "amount_unit": frequency.get("amount_unit", ""),
        "times_per_day": frequency.get("times_per_day"),
        "parse_confidence": frequency.get("parse_confidence", "unknown"),
        "frequency_cache_hit": frequency.get("frequency_cache_hit", False),
        "cached_translation": frequency.get("cached_translation", ""),
        "calculation_allowed": bool(daily is not None or frequency.get("calculation_allowed", False)),
        "no_calculation_reason": "" if daily is not None else frequency.get("no_calculation_reason", ""),
        "dose_calculation_reason": reason,
    }



def _is_liquid_strength(value: Any) -> bool:
    return bool(re.search(r"/\s*\d*(?:\.\d+)?\s*ml\b", _clean(value), re.IGNORECASE))


def _is_plain_mg_strength(value: Any) -> bool:
    text = _clean(value)
    return bool(re.search(r"\b\d+(?:\.\d+)?\s*mg\b", text, re.IGNORECASE)) and not _is_liquid_strength(text)

def _written_form(raw: str) -> str:
    head = _key(_before_dose_parentheses(raw))
    for form in _FORM_TERMS:
        if _key(form) in head:
            return form
    return ""


def _solid_form_prior(row: "_MedicineRow", raw: str) -> int:
    """Prefer plausible solid oral products when raw text has solid mg strength.

    This is a generic ranking heuristic for ambiguous brand variants. It is not
    drug-specific: a raw medicine like ``Ace 5000 mg`` has no liquid marker, so
    tablet/capsule/caplet variants are more plausible reference products than
    syrup/suspension/drop/suppository/topical variants.
    """
    raw_text = _key(raw)
    form = _key(row.dosage_form)
    written_strength = _written_strength(raw)
    has_solid_strength = bool(
        written_strength
        and re.search(r"\b\d+(?:\.\d+)?\s*mg\b", written_strength, re.IGNORECASE)
        and not re.search(r"/\s*\d*(?:\.\d+)?\s*ml\b", written_strength, re.IGNORECASE)
    )
    has_liquid_hint = bool(
        re.search(r"\b(syrup|suspension|drop|drops|ml|teaspoon|spoon|চামচ)\b", raw_text, re.IGNORECASE)
    )
    if not has_solid_strength or has_liquid_hint:
        return 0
    if re.search(r"\b(tablet|capsule|caplet)\b", form, re.IGNORECASE):
        return 2
    if re.search(r"\b(syrup|suspension|drop|solution|suppository|cream|ointment|gel)\b", form, re.IGNORECASE):
        return -1
    return 0


def _unmatched_head(raw: str) -> str:
    head = _before_dose_parentheses(raw)
    strength = _STRENGTH.search(head)
    if strength:
        head = head[: strength.start()].strip(" -:/")
    for form in _FORM_TERMS:
        head = re.sub(rf"(?i)\b{re.escape(form)}\b", " ", head)
    return _clean(head)


def _generic_core_key(value: Any) -> str:
    tokens = [
        token
        for token in _key(value).split()
        if token and token not in _SALT_WORDS and token != "+"
    ]
    return " ".join(tokens)


def _generic_combo_key(value: Any) -> str:
    text = re.sub(r"\s*\+\s*", " ", _key(value))
    tokens = [token for token in text.split() if token and token not in _SALT_WORDS]
    return " ".join(tokens)


def _looks_like_combo_name(value: Any) -> bool:
    text = _key(value)
    return "+" in text or len(_generic_combo_key(value).split()) >= 2


@dataclass(frozen=True, slots=True)
class _MedicineRow:
    brand_name: str
    generic_name: str
    dosage_form: str
    strength: str


@dataclass(frozen=True, slots=True)
class _AliasRule:
    aliases: tuple[str, ...]
    generic_name_candidates: tuple[str, ...]
    known_supportive_item: bool
    supportive_type: str
    requires_meal_timing: bool
    dosage_form: str
    strength: str


class MedicineMapper:
    """Load deterministic CSV indexes and normalize medication text."""

    def __init__(
        self,
        medicine_csv: str | Path | None = None,
        generic_csv: str | Path | None = None,
        alias_path: str | Path | None = None,
        spoon_ml: float | None = None,
    ) -> None:
        package_dir = Path(__file__).resolve().parent
        data_candidates = (package_dir / "data", package_dir.parent / "data")
        data_dir = next(
            (candidate for candidate in data_candidates if (candidate / "medicine.csv").is_file()),
            data_candidates[0],
        )
        self.medicine_csv = Path(medicine_csv) if medicine_csv else data_dir / "medicine.csv"
        self.generic_csv = Path(generic_csv) if generic_csv else data_dir / "generic.csv"
        default_alias_path = package_dir / "configs" / "medicine_aliases.yaml"
        self.alias_path = Path(alias_path) if alias_path else default_alias_path
        self.spoon_ml = spoon_ml
        self._generic_ids = self._load_generic_ids(self.generic_csv)
        self._brand_index: dict[str, list[_MedicineRow]] = {}
        self._generic_product_index: dict[str, list[_MedicineRow]] = {}
        self._generic_core_index: dict[str, list[str]] = {}
        self._generic_combo_index: dict[str, list[str]] = {}
        self._generic_identity_index: dict[str, list[str]] = {}
        self._load_medicines(self.medicine_csv)
        for generic_key in self._generic_ids:
            self._generic_core_index.setdefault(_generic_core_key(generic_key), []).append(generic_key)
            if _looks_like_combo_name(generic_key):
                self._generic_combo_index.setdefault(_generic_combo_key(generic_key), []).append(generic_key)
            identity = self._generic_identity_key(generic_key)
            if identity:
                self._generic_identity_index.setdefault(identity, []).append(generic_key)
        self._alias_index = self._load_aliases(self.alias_path)

    @staticmethod
    def _generic_identity_key(value: Any) -> str:
        """Return a salt-insensitive, component-order-insensitive identity.

        This is intentionally used only when the identity maps to one generic
        monograph. It must never turn a similar-looking ingredient into a match.
        """
        components = [
            _generic_core_key(component)
            for component in re.split(r"\s*\+\s*", _clean(value))
        ]
        return " + ".join(sorted(component for component in components if component))

    def _resolve_generic_identity(self, value: Any) -> tuple[str, str]:
        exact = self._generic_ids.get(_key(value))
        if exact:
            return exact
        identity = self._generic_identity_key(value)
        candidates = list(dict.fromkeys(self._generic_identity_index.get(identity, [])))
        if len(candidates) == 1:
            return self._generic_ids[candidates[0]]
        return "", _clean(value)

    @staticmethod
    def _load_generic_ids(path: Path) -> dict[str, tuple[str, str]]:
        result: dict[str, tuple[str, str]] = {}
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                name = _clean(row.get("generic name"))
                generic_id = _clean(row.get("generic id"))
                if name:
                    result[_key(name)] = (generic_id, name)
        return result

    def _load_medicines(self, path: Path) -> None:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for source in csv.DictReader(handle):
                row = _MedicineRow(
                    brand_name=_clean(source.get("brand_name")),
                    generic_name=_clean(source.get("generic")),
                    dosage_form=_clean(source.get("dosage form")),
                    strength=_clean(source.get("strength")),
                )
                if row.brand_name and row.generic_name:
                    self._brand_index.setdefault(_key(row.brand_name), []).append(row)
                    self._generic_product_index.setdefault(_key(row.generic_name), []).append(row)

    @staticmethod
    def _as_tuple(value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            values = [value]
        else:
            values = list(value) if isinstance(value, Iterable) else [value]
        return tuple(_clean(item) for item in values if _clean(item))

    def _load_aliases(self, path: Path) -> dict[str, _AliasRule]:
        if not path.is_file():
            return {}
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        rows = payload.get("aliases", []) if isinstance(payload, dict) else []
        result: dict[str, _AliasRule] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            aliases = self._as_tuple(row.get("aliases") or row.get("alias"))
            if not aliases:
                continue
            rule = _AliasRule(
                aliases=aliases,
                generic_name_candidates=self._as_tuple(
                    row.get("generic_name_candidates") or row.get("generic_name")
                ),
                known_supportive_item=bool(row.get("known_supportive_item", False)),
                supportive_type=_clean(row.get("supportive_type")),
                requires_meal_timing=bool(row.get("requires_meal_timing", True)),
                dosage_form=_clean(row.get("dosage_form")),
                strength=_clean(row.get("strength")),
            )
            for alias in aliases:
                result[_key(alias)] = rule
        return result

    @staticmethod
    def _matching_name(head: str, names: Iterable[str]) -> str | None:
        normalized = _key(head)
        matches = [name for name in names if normalized == name or normalized.startswith(name + " ")]
        return max(matches, key=len) if matches else None

    @staticmethod
    def _pick_variant(candidates: list[_MedicineRow], raw: str) -> _MedicineRow:
        wanted_strength = _compact(_written_strength(raw))
        wanted_strength_mg = _mg_from_strength_text(_written_strength(raw))
        wanted_form = _key(_written_form(raw))

        def score(row: _MedicineRow) -> tuple[int, int, int]:
            row_strength_mg = _mg_from_strength_text(row.strength)
            strength_match = bool(
                wanted_strength
                and (
                    _compact(row.strength) == wanted_strength
                    or (
                        wanted_strength_mg is not None
                        and row_strength_mg is not None
                        and abs(wanted_strength_mg - row_strength_mg) < 0.001
                    )
                )
            )
            form_key = _key(row.dosage_form)
            form_match = bool(
                wanted_form and (wanted_form in form_key or form_key in wanted_form)
            )
            return int(strength_match), int(form_match), _solid_form_prior(row, raw)

        return max(candidates, key=score)

    @staticmethod
    def _candidate_matches(rows: list[_MedicineRow]) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        seen: set[tuple[str, str, str]] = set()
        for row in rows:
            key = (row.generic_name, row.dosage_form, row.strength)
            if key in seen:
                continue
            seen.add(key)
            result.append(
                {
                    "generic_name": row.generic_name,
                    "dosage_form": row.dosage_form,
                    "strength": row.strength,
                }
            )
        return result[:10]

    def _parsed_fields(self, raw: str, fallback_strength: str = "") -> dict[str, Any]:
        strength_value, strength_unit = _strength_value_unit(raw, fallback_strength)
        return {
            "name_core": _unmatched_head(raw),
            "strength_value": strength_value,
            "strength_unit": strength_unit,
            "dosage_form_hint": _written_form(raw),
            # Keep this as an explicit normalized field in addition to raw_text.
            # Audit tasks can therefore pass the exact Bengali/English instruction
            # to LLM2 without relying on it being rediscovered inside a combined
            # medication display string.
            "instructions": _written_instruction(raw),
        }

    @staticmethod
    def _add_component_dosing(result: dict[str, Any]) -> None:
        components = _component_strengths(result.get("generic_name", ""), result.get("strength", ""))
        units_per_day = result.get("units_per_day")
        max_units_per_admin = result.get("max_units_per_administration")
        try:
            units = float(units_per_day) if units_per_day is not None else None
        except (TypeError, ValueError):
            units = None
        try:
            max_units = float(max_units_per_admin) if max_units_per_admin is not None else None
        except (TypeError, ValueError):
            max_units = None
        for component in components:
            strength_mg = component.get("strength_mg")
            component["calculated_daily_dose_mg"] = (
                round(float(strength_mg) * units, 3)
                if strength_mg is not None and units is not None
                else None
            )
            component["calculated_max_per_administration_mg"] = (
                round(float(strength_mg) * max_units, 3)
                if strength_mg is not None and max_units is not None
                else None
            )
        result["component_strengths"] = components
        primary = components[0] if components else {}
        result["primary_component_name"] = primary.get("component_name", result.get("generic_name", ""))
        result["primary_strength_mg"] = primary.get("strength_mg")
        result["calculated_primary_daily_dose_mg"] = primary.get("calculated_daily_dose_mg")
        result["calculated_primary_max_per_administration_mg"] = primary.get("calculated_max_per_administration_mg")

    def _alias_result(self, raw: str, head: str) -> dict[str, Any] | None:
        alias_key = self._matching_name(head, self._alias_index)
        if not alias_key:
            return None
        rule = self._alias_index[alias_key]
        generic_id = ""
        generic_name = ""
        for candidate in rule.generic_name_candidates:
            resolved = self._resolve_generic_identity(candidate)
            if resolved[0]:
                generic_id, generic_name = resolved
                break
        matched = bool(generic_id)
        result = {
            "raw_text": raw,
            "brand_name": _unmatched_head(raw) or next(iter(rule.aliases), ""),
            "generic_name": generic_name,
            "generic_id": generic_id,
            "dosage_form": rule.dosage_form or _written_form(raw),
            "strength": rule.strength or _written_strength(raw),
            "matched": matched,
            "match_confidence": "alias_exact" if matched else "known_supportive_item",
            "known_supportive_item": rule.known_supportive_item,
            "supportive_type": rule.supportive_type,
            "requires_meal_timing": rule.requires_meal_timing,
        }
        result.update(_dose_calculation(raw, result["strength"], spoon_ml=self.spoon_ml))
        self._add_component_dosing(result)
        result.update(self._parsed_fields(raw, result["strength"]))
        result["matching_reason"] = "exact normalized medicine alias"
        result["candidate_matches"] = []
        return result

    def _result(
        self,
        raw: str,
        row: _MedicineRow,
        *,
        direct_generic: bool,
    ) -> dict[str, Any]:
        generic_id, canonical_generic = self._resolve_generic_identity(row.generic_name)
        result = {
            "raw_text": raw,
            "brand_name": "" if direct_generic else row.brand_name,
            "generic_name": canonical_generic,
            "generic_id": generic_id,
            "dosage_form": row.dosage_form,
            "strength": row.strength,
            "matched": True,
            "match_confidence": "exact",
        }
        result.update(_dose_calculation(raw, result["strength"], spoon_ml=self.spoon_ml))
        self._add_component_dosing(result)
        result.update(self._parsed_fields(raw, result["strength"]))
        result["matching_reason"] = "exact generic match" if direct_generic else "exact brand match"
        result["candidate_matches"] = []
        return result

    def _inferred_generic_result(self, raw: str, head: str) -> dict[str, Any] | None:
        core = _generic_core_key(_unmatched_head(raw) or head)
        if not core:
            return None
        generic_keys = list(dict.fromkeys(self._generic_core_index.get(core, [])))
        if not generic_keys:
            generic_keys = [
                generic_key
                for generic_key in self._generic_ids
                if generic_key.startswith(core + " ")
                or _generic_core_key(generic_key) == core
            ]
        if not generic_keys:
            return None

        rows: list[_MedicineRow] = []
        for generic_key in generic_keys:
            rows.extend(self._generic_product_index.get(generic_key, []))
            if generic_key not in self._generic_product_index:
                _, name = self._generic_ids[generic_key]
                rows.append(_MedicineRow("", name, "", ""))

        wanted_strength = _compact(_written_strength(raw))
        wanted_strength_mg = _mg_from_strength_text(_written_strength(raw))
        wanted_form = _key(_written_form(raw))

        def score(row: _MedicineRow) -> tuple[int, int, int]:
            row_strength_mg = _mg_from_strength_text(row.strength)
            strength_match = bool(
                wanted_strength
                and (
                    _compact(row.strength) == wanted_strength
                    or (
                        wanted_strength_mg is not None
                        and row_strength_mg is not None
                        and abs(wanted_strength_mg - row_strength_mg) < 0.001
                    )
                )
            )
            form_key = _key(row.dosage_form)
            form_match = bool(
                wanted_form and (wanted_form in form_key or form_key in wanted_form)
            )
            return int(strength_match), int(form_match), _solid_form_prior(row, raw)

        scored = [(score(row), row) for row in rows]
        best_score = max((item[0] for item in scored), default=(0, 0))
        best_rows = [row for row_score, row in scored if row_score == best_score]
        best_generic_keys = {
            _key(row.generic_name)
            for row in best_rows
            if _key(row.generic_name) in self._generic_ids
        }
        all_generic_keys = {
            _key(row.generic_name)
            for row in rows
            if _key(row.generic_name) in self._generic_ids
        }
        safe_keys = best_generic_keys if any(best_score) else all_generic_keys

        if len(safe_keys) == 1:
            generic_key = next(iter(safe_keys))
            generic_id, generic_name = self._generic_ids[generic_key]
            candidates = [row for row in rows if _key(row.generic_name) == generic_key]
            row = self._pick_variant(candidates, raw) if candidates else _MedicineRow("", generic_name, "", "")
            result = {
                "raw_text": raw,
                "brand_name": "",
                "generic_name": generic_name,
                "generic_id": generic_id,
                "dosage_form": row.dosage_form or _written_form(raw),
                "strength": row.strength or _written_strength(raw),
                "matched": True,
                "match_confidence": "inferred_high",
                "matching_reason": (
                    "unambiguous generic-core match"
                    + (" with strength/form support" if any(best_score) else "")
                ),
                "candidate_matches": self._candidate_matches(candidates or rows),
            }
            result.update(_dose_calculation(raw, result["strength"], spoon_ml=self.spoon_ml))
            self._add_component_dosing(result)
            result.update(self._parsed_fields(raw, result["strength"]))
            return result

        if len(all_generic_keys) > 1:
            result = {
                "raw_text": raw,
                "brand_name": _unmatched_head(raw),
                "generic_name": "",
                "generic_id": "",
                "dosage_form": _written_form(raw),
                "strength": _written_strength(raw),
                "matched": False,
                "match_confidence": "inferred_needs_review",
                "matching_reason": "ambiguous generic-core candidates; generic_id not assigned",
                "candidate_matches": self._candidate_matches(rows),
            }
            result.update(_dose_calculation(raw, result["strength"], spoon_ml=self.spoon_ml))
            self._add_component_dosing(result)
            result.update(self._parsed_fields(raw, result["strength"]))
            return result
        return None

    def _inferred_combination_result(self, raw: str, head: str) -> dict[str, Any] | None:
        combo = _generic_combo_key(_unmatched_head(raw) or head)
        if not combo or len(combo.split()) < 2:
            return None
        generic_keys = list(dict.fromkeys(self._generic_combo_index.get(combo, [])))
        if not generic_keys:
            generic_keys = [
                generic_key
                for generic_key in self._generic_ids
                if _looks_like_combo_name(generic_key)
                and _generic_combo_key(generic_key) == combo
            ]
        if not generic_keys:
            return None

        rows: list[_MedicineRow] = []
        for generic_key in generic_keys:
            rows.extend(self._generic_product_index.get(generic_key, []))
            if generic_key not in self._generic_product_index:
                _, name = self._generic_ids[generic_key]
                rows.append(_MedicineRow("", name, "", ""))

        wanted_strength = _compact(_written_strength(raw))
        wanted_strength_mg = _mg_from_strength_text(_written_strength(raw))
        wanted_form = _key(_written_form(raw))

        def score(row: _MedicineRow) -> tuple[int, int, int]:
            row_strength_mg = _mg_from_strength_text(row.strength)
            strength_match = bool(
                wanted_strength
                and (
                    _compact(row.strength) == wanted_strength
                    or (
                        wanted_strength_mg is not None
                        and row_strength_mg is not None
                        and abs(wanted_strength_mg - row_strength_mg) < 0.001
                    )
                )
            )
            form_key = _key(row.dosage_form)
            form_match = bool(wanted_form and (wanted_form in form_key or form_key in wanted_form))
            return int(strength_match), int(form_match), _solid_form_prior(row, raw)

        scored = [(score(row), row) for row in rows]
        best_score = max((item[0] for item in scored), default=(0, 0, 0))
        best_rows = [row for row_score, row in scored if row_score == best_score]
        best_generic_keys = {
            _key(row.generic_name)
            for row in best_rows
            if _key(row.generic_name) in self._generic_ids
        }
        all_generic_keys = {
            _key(row.generic_name)
            for row in rows
            if _key(row.generic_name) in self._generic_ids
        }
        safe_keys = best_generic_keys if any(best_score) else all_generic_keys

        if len(safe_keys) == 1:
            generic_key = next(iter(safe_keys))
            generic_id, generic_name = self._generic_ids[generic_key]
            candidates = [row for row in rows if _key(row.generic_name) == generic_key]
            row = self._pick_variant(candidates, raw) if candidates else _MedicineRow("", generic_name, "", "")
            result = {
                "raw_text": raw,
                "brand_name": "",
                "generic_name": generic_name,
                "generic_id": generic_id,
                "dosage_form": row.dosage_form or _written_form(raw),
                "strength": row.strength or _written_strength(raw),
                "matched": True,
                "match_confidence": "inferred_high",
                "matching_reason": "unambiguous generic-combination match"
                + (" with strength/form support" if any(best_score) else ""),
                "candidate_matches": self._candidate_matches(candidates or rows),
            }
            result.update(_dose_calculation(raw, result["strength"], spoon_ml=self.spoon_ml))
            self._add_component_dosing(result)
            result.update(self._parsed_fields(raw, result["strength"]))
            return result

        result = {
            "raw_text": raw,
            "brand_name": _unmatched_head(raw),
            "generic_name": "",
            "generic_id": "",
            "dosage_form": _written_form(raw),
            "strength": _written_strength(raw),
            "matched": False,
            "match_confidence": "inferred_needs_review",
            "matching_reason": "ambiguous generic-combination candidates; generic_id not assigned",
            "candidate_matches": self._candidate_matches(rows),
        }
        result.update(_dose_calculation(raw, result["strength"], spoon_ml=self.spoon_ml))
        self._add_component_dosing(result)
        result.update(self._parsed_fields(raw, result["strength"]))
        return result

    def map_one(self, medicine_text: Any) -> dict[str, Any]:
        raw = str(medicine_text or "").strip()
        head = _before_dose_parentheses(raw)

        alias_result = self._alias_result(raw, head)
        if alias_result:
            return alias_result

        combo_result = self._inferred_combination_result(raw, head)
        if combo_result:
            return combo_result

        generic_key = self._matching_name(head, self._generic_ids)
        if generic_key:
            generic_id, generic_name = self._generic_ids[generic_key]
            candidates = self._generic_product_index.get(generic_key, [])
            if candidates:
                row = self._pick_variant(candidates, raw)
                result = self._result(raw, row, direct_generic=True)
                result["generic_id"] = generic_id
                result["generic_name"] = generic_name
                return result
            result = {
                "raw_text": raw,
                "brand_name": "",
                "generic_name": generic_name,
                "generic_id": generic_id,
                "dosage_form": _written_form(raw),
                "strength": _written_strength(raw),
                "matched": True,
                "match_confidence": "exact",
            }
            result.update(_dose_calculation(raw, result["strength"], spoon_ml=self.spoon_ml))
            self._add_component_dosing(result)
            result.update(self._parsed_fields(raw, result["strength"]))
            result["matching_reason"] = "exact generic match"
            result["candidate_matches"] = []
            return result

        brand_key = self._matching_name(head, self._brand_index)
        if brand_key:
            row = self._pick_variant(self._brand_index[brand_key], raw)
            inferred = self._inferred_generic_result(raw, head)
            # If the written name is also a generic core (e.g. Azithromycin) but
            # the exact brand row only has a liquid strength, do not compare a
            # plain 500 mg prescription against 200 mg/5 ml suspension. Prefer
            # the generic-core product variant when it resolves to the same
            # generic with a solid/plain mg strength.
            if (
                inferred
                and _key(inferred.get("generic_name", "")) == _key(row.generic_name)
                and _is_plain_mg_strength(_written_strength(raw))
                and _is_liquid_strength(row.strength)
                and not _is_liquid_strength(inferred.get("strength", ""))
            ):
                inferred["matching_reason"] = "generic-core match preferred over liquid-only brand variant"
                return inferred
            return self._result(raw, row, direct_generic=False)

        inferred = self._inferred_generic_result(raw, head)
        if inferred:
            return inferred

        result = {
            "raw_text": raw,
            "brand_name": _unmatched_head(raw),
            "generic_name": "",
            "generic_id": "",
            "dosage_form": _written_form(raw),
            "strength": _written_strength(raw),
            "matched": False,
            "match_confidence": "unmatched",
            "matching_reason": "no exact or safe inferred match",
            "candidate_matches": [],
        }
        result.update(_dose_calculation(raw, result["strength"], spoon_ml=self.spoon_ml))
        self._add_component_dosing(result)
        result.update(self._parsed_fields(raw, result["strength"]))
        return result

    def map(self, medication_text: Any) -> list[dict[str, Any]]:
        return [self.map_one(item) for item in split_medicines(medication_text)]


def map_medicines(
    medication_text: Any,
    medicine_csv: str | Path | None = None,
    generic_csv: str | Path | None = None,
    alias_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Convenience entry point for one medication field."""
    return MedicineMapper(medicine_csv, generic_csv, alias_path).map(medication_text)


__all__ = ["MedicineMapper", "map_medicines", "split_medicines"]
