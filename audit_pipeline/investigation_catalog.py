"""Ibn Sina investigation catalogue and conservative name matching.

The catalogue establishes test identity and local availability only. Clinical
relevance remains a patient-specific decision made from demographics,
complaints, history, screening, examination, results, and diagnosis.
"""

from __future__ import annotations

import csv
import re
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any

LEGACY_CATALOG_PATH = Path(__file__).resolve().parent / "data" / "bd_investigation_catalog.csv"
CATALOG_PATH = Path(__file__).resolve().parent / "data" / "ibnsina_investigation_catalog.csv"
IBNSINA_CATALOG_PATH = CATALOG_PATH

# Ibn Sina's PCR page contains three bare medicine names as service labels.
# Preserve those raw labels for provenance/search, but qualify what the doctor
# sees and sends to the audit so they are not mistaken for prescriptions.
SOURCE_LABEL_OVERRIDES = {
    ("pcr lab", "aspirin"): "Aspirin response test (PCR laboratory)",
    ("pcr lab", "clopidogrel"): "Clopidogrel response test (PCR laboratory)",
    ("pcr lab", "warfarin"): "Warfarin sensitivity test (PCR laboratory)",
}


def _key(value: Any) -> str:
    text = str(value or "").casefold()
    text = re.sub(r"\b(?:govt?\.?\s*rate|government\s*rate)\b", " ", text)
    text = text.replace("&", " and ")
    text = re.sub(r"\bserum\b", " s ", text)
    text = re.sub(r"\bultrasound\b", " usg ", text)
    text = re.sub(r"\belectrocardiogram\b", " ecg ", text)
    text = re.sub(r"\bechocardiogram\b", " echo ", text)
    text = re.sub(r"\bcomputed\s+tomography\b", " ct ", text)
    text = re.sub(r"\bmagnetic\s+resonance\s+imaging\b", " mri ", text)
    text = re.sub(r"[^a-z0-9+]+", " ", text)
    return " ".join(text.split())


def _aliases(row: dict[str, Any]) -> set[str]:
    aliases = {
        _key(row.get("display_name")),
        _key(row.get("canonical_name")),
        _key(row.get("source_display_name")),
    }
    display = str(row.get("display_name", ""))
    aliases.add(_key(re.sub(r"^(?:s\.|cr\.)\s*", "", display, flags=re.I)))
    return {value for value in aliases if value}


@lru_cache(maxsize=1)
def load_catalog() -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    with CATALOG_PATH.open(encoding="utf-8", newline="") as handle:
        for index, raw in enumerate(csv.DictReader(handle), start=1):
            source_category = raw["source_category"].strip()
            source_display_name = raw["display_name"].strip()
            qualified_name = SOURCE_LABEL_OVERRIDES.get(
                (source_category.casefold(), source_display_name.casefold()),
                source_display_name,
            )
            row = {
                "source_no": index,
                "category": source_category,
                "source_category": source_category,
                "display_name": qualified_name,
                "canonical_name": qualified_name,
                "source_display_name": source_display_name,
                "source": "The Ibn Sina Trust",
                "source_url": raw["source_url"].strip(),
            }
            row["aliases"] = sorted(_aliases(row))
            rows.append(row)
    return tuple(rows)


@lru_cache(maxsize=1)
def _alias_index() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in load_catalog():
        for alias in row["aliases"]:
            result.setdefault(alias, row)
    return result


@lru_cache(maxsize=2048)
def _match_normalized_investigation(
    query: str,
    fuzzy_threshold: float,
) -> dict[str, Any] | None:
    if not query:
        return None
    exact = _alias_index().get(query)
    if exact:
        return {**exact, "match_type": "catalog_exact", "score": 1.0}

    best_row: dict[str, Any] | None = None
    best_score = 0.0
    for alias, row in _alias_index().items():
        score = SequenceMatcher(None, query, alias).ratio()
        if score > best_score:
            best_row, best_score = row, score
    if best_row and best_score >= fuzzy_threshold:
        return {**best_row, "match_type": "catalog_fuzzy", "score": round(best_score, 3)}
    return None


def match_investigation(value: Any, *, fuzzy_threshold: float = 0.86) -> dict[str, Any] | None:
    """Match once per normalized term and reuse it across repeated live checks."""
    return _match_normalized_investigation(_key(value), float(fuzzy_threshold))


def investigation_categories() -> list[dict[str, Any]]:
    """Return browseable catalogue categories and their source-row counts."""
    counts: dict[str, int] = {}
    for row in load_catalog():
        category = row["category"]
        counts[category] = counts.get(category, 0) + 1
    return [
        {"category": category, "count": count}
        for category, count in sorted(counts.items(), key=lambda item: item[0].casefold())
    ]


def search_investigations(
    query: Any,
    limit: int = 12,
    *,
    category: str | None = None,
) -> list[dict[str, Any]]:
    needle = _key(query)
    selected_category = str(category or "").strip().casefold()
    if not needle and not selected_category:
        return []
    tokens = set(needle.split())
    scored: list[tuple[float, int, dict[str, Any]]] = []
    for row in load_catalog():
        if selected_category and row["category"].casefold() != selected_category:
            continue
        aliases = row["aliases"]
        if not needle:
            scored.append((1.0, -row["source_no"], row))
            continue
        exact = needle in aliases
        prefix = any(alias.startswith(needle) for alias in aliases)
        contains = any(needle in alias for alias in aliases)
        token_overlap = max(
            (len(tokens.intersection(alias.split())) / max(1, len(tokens)) for alias in aliases),
            default=0.0,
        )
        fuzzy = max((SequenceMatcher(None, needle, alias).ratio() for alias in aliases), default=0.0)
        score = 4.0 if exact else 3.0 if prefix else 2.0 if contains else token_overlap + fuzzy
        if score >= 0.82:
            scored.append((score, -row["source_no"], row))
    if needle:
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    else:
        scored.sort(key=lambda item: item[2]["display_name"].casefold())
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _, _, row in scored:
        identity = _key(row["canonical_name"] or row["display_name"])
        # Category browsing is a source-faithful inventory: retain every item
        # the provider lists, including spelling/punctuation variants. Typed
        # global search remains deduplicated for concise suggestions.
        if needle and identity in seen:
            continue
        seen.add(identity)
        results.append(
            {
                "source_no": row["source_no"],
                "category": row["category"],
                "display_name": row["display_name"],
                "canonical_name": row["canonical_name"],
                "source": row["source"],
                "source_category": row["source_category"],
                "source_display_name": row["source_display_name"],
            }
        )
        if len(results) >= max(1, min(int(limit or 12), 500)):
            break
    return results


__all__ = [
    "CATALOG_PATH",
    "IBNSINA_CATALOG_PATH",
    "LEGACY_CATALOG_PATH",
    "SOURCE_LABEL_OVERRIDES",
    "investigation_categories",
    "load_catalog",
    "match_investigation",
    "search_investigations",
]
