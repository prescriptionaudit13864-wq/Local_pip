"""Deterministic parser for common Bangladesh prescription frequency strings.

The optional frequency cache is a normalization / semantic-hint layer. It is
not final dosing authority. The parser still classifies raw text and cached
translations conservatively, and dose calculation only uses safe categories.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any


_DIGIT_TRANSLATION = str.maketrans(
    {
        "\u09e6": "0", "\u09e7": "1", "\u09e8": "2", "\u09e9": "3", "\u09ea": "4",
        "\u09eb": "5", "\u09ec": "6", "\u09ed": "7", "\u09ee": "8", "\u09ef": "9",
        "\u0660": "0", "\u0661": "1", "\u0662": "2", "\u0663": "3", "\u0664": "4",
        "\u0665": "5", "\u0666": "6", "\u0667": "7", "\u0668": "8", "\u0669": "9",
        "\u06f0": "0", "\u06f1": "1", "\u06f2": "2", "\u06f3": "3", "\u06f4": "4",
        "\u06f5": "5", "\u06f6": "6", "\u06f7": "7", "\u06f8": "8", "\u06f9": "9",
    }
)

_DEFAULT_CACHE_PATH = Path(__file__).resolve().parent / "configs" / "frequency_cache.json"
_PARENS = re.compile(r"\(([^()]*)\)")
_JSON_INSTRUCTION = re.compile(r'"(?:additionalInstructions|instructions)"\s*:\s*"([^"]*)"', re.IGNORECASE)
_FIXED_SCHEDULE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?(?:\s*[+-]\s*\d+(?:\.\d+)?){1,5})(?![\w.])")
_AMOUNT = r"(?P<amount>half|a half|one half|\d+\s*/\s*\d+|\d+(?:\.\d+)?)"
_SPOON_PATTERN = re.compile(
    rf"{_AMOUNT}\s*(?P<unit>চা-?চামচ|চামচ|چامچ|\u0686\u09be\u09ae\u0686|spoons?|teaspoons?|tsp)\s*"
    r"(?:করে\s*)?(?:দিনে\s*)?(?:x|×|\*|\s+)?\s*(?P<times>\d+(?:\.\d+)?)\s*(?:বার|بار|times?)",
    re.IGNORECASE,
)
_ML_PATTERN = re.compile(
    rf"{_AMOUNT}\s*(?P<unit>ml|মি\.?\s*লি\.?|মিলি)\s*(?:করে\s*)?(?:দিনে\s*)?(?:x|×|\*|\s+)?\s*"
    r"(?P<times>\d+(?:\.\d+)?)\s*(?:বার|بار|times?)",
    re.IGNORECASE,
)
_AS_NEEDED = re.compile(r"\b(?:sos|prn|as needed)\b|প্রয়োজন\s*(?:মত|ে)|প্রয়োজন\s*(?:মত|ে)", re.IGNORECASE)
_STAT = re.compile(r"\b(?:stat|once now|single dose)\b|এখনই", re.IGNORECASE)
_RECONSTITUTION = re.compile(r"আধা\s*লিটার|half\s+a?\s*liter|half\s+litre|পানিতে|water|packet|پ্যাকেট|প্যাকেট", re.IGNORECASE)
_TOPICAL = re.compile(r"affected area|আক্রান্ত\s+স্থানে|লাগাবেন|apply|neck to toe|গলা থেকে পা", re.IGNORECASE)
_DROP = re.compile(r"drop|drops|ফোঁটা|ফোটা|نাকে|নাকে|nostril|eye|চোখ", re.IGNORECASE)
_WEEKLY = re.compile(r"weekly|per week|প্রতি\s*সপ্তাহ|সপ্তাহে", re.IGNORECASE)
_ALTERNATE_DAY = re.compile(r"alternate\s+days?|every\s+other\s+day|এক\s*দিন\s*পরপর", re.IGNORECASE)
_PUFF_PATTERN = re.compile(r"(?P<amount>\d+(?:\.\d+)?)\s*(?:puffs?|পাফ).*?(?:দিনে\s*)?(?P<times>\d+(?:\.\d+)?)\s*(?:বার|times?)", re.IGNORECASE)
_DROP_TIMES = re.compile(r"(?P<amount>\d+(?:\.\d+)?)\s*(?:drops?|ফোঁটা|ফোটা).*?(?:দিনে\s*)?(?P<times>\d+(?:\.\d+)?)\s*(?:বার|times?)", re.IGNORECASE)
_UNIT_TIMES = re.compile(r"(?P<amount>\d+(?:\.\d+)?)\s*টি\s*(?:দিনে\s*)?(?P<times>\d+(?:\.\d+)?)\s*বার", re.IGNORECASE)
_UNIT_NIGHT = re.compile(r"(?P<amount>\d+(?:\.\d+)?)\s*টি\s*রাতে", re.IGNORECASE)
_DAILY_TIMES = re.compile(r"দিনে\s*(?P<times>\d+(?:\.\d+)?)\s*বার", re.IGNORECASE)
_HOURLY_INTERVAL = re.compile(r"প্রতি\s*(?P<hours>\d+(?:\.\d+)?)\s*ঘণ্টা\s*পরপর|every\s*(?P<hours_en>\d+(?:\.\d+)?)\s*hours?", re.IGNORECASE)
_TRANSLATED_TABLET_SCHEDULE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:tablets?|capsules?|tabs?)\b", re.IGNORECASE)
_TRANSLATED_SPOON = re.compile(r"(half|a half|one half|\d+(?:\.\d+)?)\s*spoons?\s+(\d+(?:\.\d+)?)\s*times?", re.IGNORECASE)
_TRANSLATED_DROP = re.compile(r"(\d+(?:\.\d+)?)\s*drops?.*(nostrils?|eyes?)", re.IGNORECASE)
_TRANSLATED_TOPICAL_TIMES = re.compile(r"(\d+(?:\.\d+)?)\s*times?\s+a\s+day", re.IGNORECASE)


def normalize_digits(value: Any) -> str:
    """Normalize English, Bengali, Arabic, and Persian digits to ASCII."""
    return str(value or "").translate(_DIGIT_TRANSLATION)


def _normalize_schedule_fractions(value: str) -> str:
    text = normalize_digits(value)
    for glyph, decimal in {"½": "0.5", "¼": "0.25", "¾": "0.75", "⅓": "0.333"}.items():
        text = text.replace(glyph, decimal)

    def fraction(match: re.Match[str]) -> str:
        numerator, denominator = float(match.group(1)), float(match.group(2))
        return f"{numerator / denominator:g}" if denominator else match.group(0)

    return re.sub(r"(?<![\d.])(\d+)\s*/\s*(\d+)(?![\d.])", fraction, text)


def _normalize_key(value: Any) -> str:
    text = normalize_digits(value).casefold().strip()
    text = text.replace("×", "x").replace("*", "x")
    return re.sub(r"\s+", " ", text)


@lru_cache(maxsize=4)
def load_frequency_cache(cache_path: str | Path | None = None) -> dict[str, str]:
    path = Path(cache_path) if cache_path else _DEFAULT_CACHE_PATH
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    cache: dict[str, str] = {}
    for key, translation in raw.items():
        cache[str(key)] = str(translation)
        cache[_normalize_key(key)] = str(translation)
    return cache


def _cache_lookup(value: str, cache_path: str | Path | None = None) -> tuple[bool, str]:
    cache = load_frequency_cache(str(cache_path) if cache_path else None)
    if value in cache:
        return True, cache[value]
    normalized = _normalize_key(value)
    if normalized in cache:
        return True, cache[normalized]
    return False, ""


def extract_raw_frequencies(text: Any) -> list[str]:
    normalized = normalize_digits(text)
    chunks = [match.group(1).strip() for match in _PARENS.finditer(normalized)]
    chunks.extend(match.group(1).strip() for match in _JSON_INSTRUCTION.finditer(normalized))
    useful = [
        chunk for chunk in chunks if chunk and (
            any(marker in chunk for marker in ("+", "-", "x", "×", "*", "/"))
            or _AS_NEEDED.search(chunk) or _STAT.search(chunk)
            or _RECONSTITUTION.search(chunk) or _TOPICAL.search(chunk)
            or _DROP.search(chunk) or _WEEKLY.search(chunk) or _ALTERNATE_DAY.search(chunk)
            or _PUFF_PATTERN.search(chunk) or _UNIT_TIMES.search(chunk)
            or _DAILY_TIMES.search(chunk) or _HOURLY_INTERVAL.search(chunk)
            or re.search(r"(?:বার|times?|spoons?|চামচ|چامچ|ml|মিলি)", chunk, re.IGNORECASE)
        )
    ]
    if useful:
        return useful
    value = normalized.strip()
    return [value] if value else []


def _number(value: str) -> float:
    text = value.casefold().replace(" ", "")
    if text in {"half", "ahalf", "onehalf"}:
        return 0.5
    if "/" in text:
        numerator, denominator = text.split("/", 1)
        return float(numerator) / float(denominator)
    return float(text)


def _base_result(
    *,
    raw_frequency: str,
    normalized_frequency: str,
    frequency_type: str,
    reason: str,
    frequency_cache_hit: bool = False,
    cached_translation: str = "",
    parse_confidence: str = "deterministic",
    calculation_allowed: bool = False,
    no_calculation_reason: str = "",
    **extra: Any,
) -> dict[str, Any]:
    result = {
        "raw_frequency": raw_frequency,
        "normalized_frequency": normalized_frequency,
        "frequency_cache_hit": frequency_cache_hit,
        "cached_translation": cached_translation,
        "frequency_type": frequency_type,
        "units_per_day": extra.pop("units_per_day", None),
        "schedule_parts": extra.pop("schedule_parts", []),
        "administrations_per_day": extra.pop("administrations_per_day", None),
        "max_units_per_administration": extra.pop("max_units_per_administration", None),
        "interval_pattern": extra.pop("interval_pattern", ""),
        "parse_confidence": parse_confidence,
        "calculation_allowed": calculation_allowed,
        "no_calculation_reason": no_calculation_reason,
        "reason": reason,
    }
    result.update(extra)
    return result


def _schedule_details(parts: list[float]) -> dict[str, Any]:
    nonzero = [part for part in parts if part > 0]
    administrations = len(nonzero)
    interval = ""
    if administrations == 4:
        interval = "q6h_like"
    elif administrations == 3:
        interval = "q8h_like"
    elif administrations == 2:
        interval = "q12h_like"
    elif administrations == 1:
        interval = "daily_like"
    return {
        "administrations_per_day": float(administrations),
        "max_units_per_administration": max(nonzero) if nonzero else 0,
        "interval_pattern": interval,
    }


def _parse_one(text: str, *, raw_frequency: str, cache_hit: bool, translation: str) -> dict[str, Any] | None:
    normalized = _normalize_schedule_fractions(text).strip()
    combined = f"{normalized} {translation}".strip()
    folded = combined.casefold()
    raw_norm = normalize_digits(raw_frequency).strip()

    if _AS_NEEDED.search(folded):
        return _base_result(raw_frequency=raw_frequency, normalized_frequency=raw_norm, frequency_cache_hit=cache_hit, cached_translation=translation, frequency_type="as_needed", calculation_allowed=False, no_calculation_reason="as-needed frequency has no fixed daily dose", reason="as-needed frequency parsed")
    if _STAT.search(folded):
        return _base_result(raw_frequency=raw_frequency, normalized_frequency=raw_norm, frequency_cache_hit=cache_hit, cached_translation=translation, frequency_type="stat_single_dose", calculation_allowed=False, no_calculation_reason="stat/single-dose frequency has no fixed daily dose", reason="stat/single-dose frequency parsed")
    if _WEEKLY.search(folded):
        return _base_result(raw_frequency=raw_frequency, normalized_frequency=raw_norm, frequency_cache_hit=cache_hit, cached_translation=translation, frequency_type="weekly_or_interval", calculation_allowed=False, no_calculation_reason="weekly/interval instruction is not converted to daily dose", reason="weekly/interval instruction parsed")
    if _ALTERNATE_DAY.search(folded):
        return _base_result(raw_frequency=raw_frequency, normalized_frequency=raw_norm, frequency_cache_hit=cache_hit, cached_translation=translation, frequency_type="alternate_day", calculation_allowed=False, no_calculation_reason="alternate-day instruction is not converted to a fixed daily dose", reason="alternate-day frequency parsed")
    if _RECONSTITUTION.search(folded):
        return _base_result(raw_frequency=raw_frequency, normalized_frequency=raw_norm, frequency_cache_hit=cache_hit, cached_translation=translation, frequency_type="reconstitution", calculation_allowed=False, no_calculation_reason="reconstitution instruction is not a daily medicine dose", reason="reconstitution instruction parsed")
    if _TOPICAL.search(folded):
        times = _TRANSLATED_TOPICAL_TIMES.search(translation) or re.search(r"দিনে\s*(\d+(?:\.\d+)?)\s*বার", normalized)
        return _base_result(raw_frequency=raw_frequency, normalized_frequency=raw_norm, frequency_cache_hit=cache_hit, cached_translation=translation, frequency_type="topical_application", times_per_day=float(times.group(1)) if times else None, calculation_allowed=False, no_calculation_reason="topical application is not converted to systemic mg/day", reason="topical application instruction parsed")
    if _DROP.search(folded):
        translated_drop = _TRANSLATED_DROP.search(translation)
        raw_drop = _DROP_TIMES.search(normalized) or re.search(r"(\d+(?:\.\d+)?)\s*(?:ফোঁটা|ফোটা|drops?)", folded)
        amount = float((translated_drop or raw_drop).group(1)) if (translated_drop or raw_drop) else None
        times = float(raw_drop.group("times")) if raw_drop and "times" in raw_drop.groupdict() else None
        return _base_result(raw_frequency=raw_frequency, normalized_frequency=raw_norm, frequency_cache_hit=cache_hit, cached_translation=translation, frequency_type="drop_instruction", amount_per_dose=amount, amount_unit="drop" if amount is not None else "", times_per_day=times, administrations_per_day=times, calculation_allowed=False, no_calculation_reason="drop instruction needs mg/drop data before mg/day calculation", reason="drop instruction parsed")

    puff = _PUFF_PATTERN.search(normalized)
    if puff:
        amount, times = float(puff.group("amount")), float(puff.group("times"))
        return _base_result(raw_frequency=raw_frequency, normalized_frequency=raw_norm, frequency_cache_hit=cache_hit, cached_translation=translation, frequency_type="inhaler_puff", amount_per_dose=amount, amount_unit="puff", times_per_day=times, administrations_per_day=times, calculation_allowed=False, no_calculation_reason="puff instruction is not converted to systemic mg/day", reason="inhaler puff frequency parsed")

    spoon = _SPOON_PATTERN.search(normalized) or _TRANSLATED_SPOON.search(translation)
    if spoon:
        if spoon.re is _TRANSLATED_SPOON:
            amount, times, unit = _number(spoon.group(1)), float(spoon.group(2)), "spoon"
        else:
            amount, times, unit = _number(spoon.group("amount")), float(spoon.group("times")), "spoon"
        return _base_result(raw_frequency=raw_frequency, normalized_frequency=raw_norm, frequency_cache_hit=cache_hit, cached_translation=translation, frequency_type="liquid_spoon_daily", amount_per_dose=amount, amount_unit=unit, times_per_day=times, units_per_day=amount * times, administrations_per_day=times, max_units_per_administration=amount, calculation_allowed=False, no_calculation_reason="spoon volume is required before mg/day calculation", reason="liquid spoon daily frequency parsed")

    ml = _ML_PATTERN.search(normalized)
    if ml:
        amount, times = _number(ml.group("amount")), float(ml.group("times"))
        return _base_result(raw_frequency=raw_frequency, normalized_frequency=raw_norm, frequency_cache_hit=cache_hit, cached_translation=translation, frequency_type="liquid_ml_daily", amount_per_dose=amount, amount_unit="ml", times_per_day=times, units_per_day=amount * times, administrations_per_day=times, max_units_per_administration=amount, calculation_allowed=True, reason="liquid ml daily frequency parsed")

    fixed = _FIXED_SCHEDULE.search(normalized)
    if fixed:
        separator = "+" if "+" in fixed.group(1) else "-"
        parts = [float(part.strip()) for part in fixed.group(1).split(separator) if part.strip()]
        return _base_result(raw_frequency=raw_frequency, normalized_frequency=raw_norm, frequency_cache_hit=cache_hit, cached_translation=translation, frequency_type="fixed_solid_daily", units_per_day=sum(parts), schedule_parts=parts, calculation_allowed=True, reason="fixed daily solid schedule parsed", **_schedule_details(parts))

    unit_times = _UNIT_TIMES.search(normalized)
    if unit_times:
        amount, times = float(unit_times.group("amount")), float(unit_times.group("times"))
        return _base_result(raw_frequency=raw_frequency, normalized_frequency=raw_norm, frequency_cache_hit=cache_hit, cached_translation=translation, frequency_type="non_oral_unit_schedule", amount_per_dose=amount, amount_unit="unit", times_per_day=times, administrations_per_day=times, calculation_allowed=False, no_calculation_reason="route-specific unit schedule is not converted to systemic mg/day", reason="route-specific unit frequency parsed")
    unit_night = _UNIT_NIGHT.search(normalized)
    if unit_night:
        amount = float(unit_night.group("amount"))
        return _base_result(raw_frequency=raw_frequency, normalized_frequency=raw_norm, frequency_cache_hit=cache_hit, cached_translation=translation, frequency_type="non_oral_unit_schedule", amount_per_dose=amount, amount_unit="unit", times_per_day=1.0, administrations_per_day=1.0, calculation_allowed=False, no_calculation_reason="route-specific unit schedule is not converted to systemic mg/day", reason="nightly route-specific unit frequency parsed")
    hourly = _HOURLY_INTERVAL.search(normalized)
    if hourly:
        hours = float(hourly.group("hours") or hourly.group("hours_en"))
        times = 24.0 / hours if hours else None
        return _base_result(raw_frequency=raw_frequency, normalized_frequency=raw_norm, frequency_cache_hit=cache_hit, cached_translation=translation, frequency_type="hourly_interval", times_per_day=times, administrations_per_day=times, interval_hours=hours, calculation_allowed=False, no_calculation_reason="route/dose amount is required before interval dosing can be converted", reason="hourly interval frequency parsed")
    daily = _DAILY_TIMES.search(normalized)
    if daily:
        times = float(daily.group("times"))
        return _base_result(raw_frequency=raw_frequency, normalized_frequency=raw_norm, frequency_cache_hit=cache_hit, cached_translation=translation, frequency_type="daily_times_unspecified_amount", times_per_day=times, administrations_per_day=times, calculation_allowed=False, no_calculation_reason="dose amount is not encoded in this frequency", reason="daily administration frequency parsed")

    translated_tablets = _TRANSLATED_TABLET_SCHEDULE.findall(translation)
    if translated_tablets:
        parts = [float(value) for value in translated_tablets]
        return _base_result(raw_frequency=raw_frequency, normalized_frequency=raw_norm, frequency_cache_hit=cache_hit, cached_translation=translation, frequency_type="fixed_solid_daily", units_per_day=sum(parts), schedule_parts=parts, calculation_allowed=True, reason="fixed daily solid schedule parsed from cached translation", **_schedule_details(parts))

    return None


def _rank_result(result: dict[str, Any], *, prefer_liquid: bool) -> tuple[int, int]:
    scores = {
        "liquid_ml_daily": 90 if prefer_liquid else 70,
        "liquid_spoon_daily": 85 if prefer_liquid else 65,
        "fixed_solid_daily": 80 if not prefer_liquid else 60,
        "topical_application": 55,
        "drop_instruction": 55,
        "inhaler_puff": 55,
        "non_oral_unit_schedule": 55,
        "hourly_interval": 52,
        "daily_times_unspecified_amount": 52,
        "reconstitution": 55,
        "weekly_or_interval": 50,
        "alternate_day": 50,
        "as_needed": 45,
        "stat_single_dose": 45,
        "unknown": 0,
    }
    return scores.get(str(result.get("frequency_type")), 0), 1 if result.get("frequency_cache_hit") else 0


def parse_frequency(text: Any, *, cache_path: str | Path | None = None, prefer_liquid: bool = False) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    raw_frequency = ""
    normalized_frequency = ""
    for candidate in extract_raw_frequencies(text):
        raw_frequency = candidate
        normalized_frequency = normalize_digits(candidate).strip()
        cache_hit, translation = _cache_lookup(candidate, cache_path)
        parsed = _parse_one(candidate, raw_frequency=candidate, cache_hit=cache_hit, translation=translation)
        if parsed:
            results.append(parsed)
        if translation:
            parsed_translation = _parse_one(translation, raw_frequency=candidate, cache_hit=True, translation=translation)
            if parsed_translation:
                results.append(parsed_translation)

    if results:
        return sorted(results, key=lambda item: _rank_result(item, prefer_liquid=prefer_liquid), reverse=True)[0]

    return _base_result(raw_frequency=raw_frequency, normalized_frequency=normalized_frequency, frequency_type="unknown", parse_confidence="unknown", calculation_allowed=False, no_calculation_reason="frequency could not be safely converted to daily units", reason="frequency could not be safely converted to daily units")


__all__ = ["extract_raw_frequencies", "load_frequency_cache", "normalize_digits", "parse_frequency"]
