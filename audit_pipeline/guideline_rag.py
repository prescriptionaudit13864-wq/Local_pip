from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LOW_VALUE_FACT_TYPES = {"storage"}
LOW_VALUE_RULE_PREFIXES = ("medicine_storage",)
SAFE_SHORT_ALIASES = {"tb", "uti", "dm", "htn", "ckd", "copd", "hiv", "aom", "ari"}
ALIAS_STOPWORDS = {
    "normal",
    "absent",
    "present",
    "high",
    "low",
    "mild",
    "moderate",
    "severe",
    "pain",
    "fever",
    "headache",
    "cough",
    "rash",
    "nausea",
    "vomiting",
    "diarrhea",
    "diarrhoea",
    "weakness",
    "bodyache",
    "dizziness",
}
NON_CONDITION_ALIASES = {
    "adult",
    "adults",
    "all",
    "all patients",
    "child",
    "children",
    "elderly",
    "female",
    "infant",
    "lactation",
    "lactating",
    "male",
    "neonate",
    "pediatric",
    "paediatric",
    "pregnancy",
    "pregnant",
    "women",
}
SYMPTOM_STOPWORDS = {
    "with",
    "without",
    "from",
    "after",
    "before",
    "normal",
    "absent",
    "present",
    "patient",
    "history",
    "diagnosis",
    "chief",
    "complaint",
    "blood",
    "pressure",
    "pulse",
    "rate",
    "temperature",
    "advice",
    "medication",
    "examination",
    "screening",
    "days",
    "day",
    "weeks",
    "week",
    "month",
    "months",
    "years",
    "year",
    "mild",
    "moderate",
    "severe",
    "male",
    "female",
    "high",
    "low",
    "nad",
}
SECTION_RELEVANT_FACT_TYPES = {
    "Dx": {
        "definition",
        "case_definition",
        "clinical_presentation",
        "symptom",
        "symptom_criteria",
        "diagnostic_clue",
        "diagnostic_criteria",
        "differential_diagnosis",
        "likely_pathogen",
        "risk_factor",
        "red_flag",
        "warning_sign",
        "test_interpretation",
        "pharmacology",
        "drug_class",
    },
    "Inv": {
        "recommended_investigation",
        "diagnostic_test",
        "test_interpretation",
        "investigation_not_needed",
        "imaging_not_needed",
        "screening_criteria",
        "specimen_collection",
        "culture_or_sensitivity",
        "monitoring",
    },
    "Rx": {
        "recommended_medicine",
        "alternative_medicine",
        "avoid_medicine",
        "no_antibiotic_care",
        "antibiotic_indication",
        "dose_or_duration",
        "route_or_site",
        "treatment_regimen",
        "weight_band_dose",
        "contraindication",
        "caution",
        "interaction",
        "precaution",
        "pregnancy_lactation",
        "pediatric_rule",
        "overdose",
        "lactation_safety",
        "pregnancy_safety",
        "aware_classification",
        "spectrum",
        "coverage",
        "monitoring",
        "indication",
    },
    "Adv": {
        "recommended_advice",
        "diet_advice",
        "exercise_advice",
        "lifestyle_advice",
        "followup",
        "referral_criteria",
        "admission_criteria",
        "infection_control",
        "public_health",
        "recording_reporting",
        "specimen_collection",
    },
}


def normalize_text(text: Any) -> str:
    text = str(text or "").lower().replace("_", " ")
    text = re.sub(r"[^a-z0-9+/\u0980-\u09ff\s-]+", " ", text)
    return " ".join(text.split())


def phrase_in_text(phrase: str, text: str) -> bool:
    """Conservative exact/alias matching.

    Long phrases may match as substrings after normalization. Short clinical
    abbreviations are allowed only from a small allow-list and must match as
    whole tokens, so we do not turn fuzzy matching into unsafe guessing.
    """
    phrase = normalize_text(phrase)
    text = normalize_text(text)
    if not phrase or not text:
        return False
    if len(phrase) <= 3 and " " not in phrase:
        if phrase not in SAFE_SHORT_ALIASES:
            return False
        return re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", text) is not None
    return phrase in text


def symptom_tokens(text: str) -> set[str]:
    """Extract conservative symptom/evidence tokens for textbook fallback retrieval."""
    text = normalize_text(text)
    tokens = set()
    for token in re.findall(r"[a-z]{4,}|[\u0980-\u09ff]{3,}", text):
        if token not in SYMPTOM_STOPWORDS and not token.isdigit():
            tokens.add(token)
    return tokens


def symptom_overlap(query_text: str, fact: dict[str, Any]) -> set[str]:
    query_tokens = symptom_tokens(query_text)
    if not query_tokens:
        return set()
    fact_text = " ".join(
        str(fact.get(k) or "")
        for k in ["condition", "fact_text", "diagnostic_criteria", "warning_signs", "recommended_tests"]
    )
    fact_tokens = symptom_tokens(fact_text)
    return query_tokens & fact_tokens


_CATEGORY_WORDS = {
    "i": "1",
    "ii": "2",
    "iii": "3",
    "iv": "4",
    "v": "5",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
}


def category_markers(text: str) -> set[str]:
    norm = normalize_text(text)
    markers: set[str] = set()
    for match in re.finditer(
        r"\b(?:category|cat|grade|stage|group|class)\s*(i{1,3}|iv|v|[1-5]|one|two|three|four|five)\b", norm
    ):
        value = match.group(1).casefold()
        markers.add(_CATEGORY_WORDS.get(value, value))
    return markers


def _source_condition_match_score(fact: dict[str, Any]) -> int:
    """Prefer facts whose own source/profile is specifically about the condition.

    This is not a clinical rule; it is retrieval hygiene. If two facts both
    mention "hypertension", a hypertension guideline should outrank a diabetes
    guideline that mentions hypertension only as a comorbidity/screening topic.
    """
    condition = normalize_text(fact.get("condition", ""))
    if not condition or condition in NON_CONDITION_ALIASES:
        return 0
    source_text = normalize_text(
        " ".join(str(fact.get(key) or "") for key in ("source_id", "source_title", "source_guideline", "profile"))
    )
    if not source_text:
        return 0
    condition_tokens = [
        token
        for token in condition.split()
        if len(token) > 3 and token not in ALIAS_STOPWORDS and token not in NON_CONDITION_ALIASES
    ]
    if condition in source_text:
        return 35
    if condition_tokens and any(token in source_text for token in condition_tokens):
        return 20
    return 0


def listify(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        values = value
    else:
        values = [value]
    out = []
    for item in values:
        if isinstance(item, dict):
            item = item.get("text") or item.get("name") or json.dumps(item, ensure_ascii=False, sort_keys=True)
        clean = str(item or "").strip()
        if clean:
            out.append(clean)
    return out


def audit_uses_for_fact(fact: dict[str, Any]) -> set[str]:
    uses = {str(item).strip() for item in listify(fact.get("audit_uses")) if str(item).strip()}
    section = str(fact.get("section") or "").strip()
    if section:
        uses.add(section)
    return uses


def fact_relevant_to_section(fact: dict[str, Any], section: str | None) -> bool:
    if not section:
        return True
    fact_type = str(fact.get("fact_type") or "")
    if section in audit_uses_for_fact(fact):
        return True
    return fact_type in SECTION_RELEVANT_FACT_TYPES.get(section, set())


@dataclass
class RetrievalResult:
    facts: list[dict[str, Any]]
    matched_conditions: list[str]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {"facts": self.facts, "matched_conditions": self.matched_conditions, "warnings": self.warnings}


class GuidelineRAG:
    def __init__(self, facts_path: str | Path = "audit_pipeline/data/rag/guideline_facts_merged.jsonl"):
        self.facts_path = Path(facts_path)
        self.facts = self._load(self.facts_path)
        self.condition_index: dict[str, list[dict[str, Any]]] = {}
        for fact in self.facts:
            for alias in self._aliases_for_fact(fact):
                self.condition_index.setdefault(alias, []).append(fact)

    @staticmethod
    def _load(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows

    def _aliases_for_fact(self, fact: dict[str, Any]) -> list[str]:
        aliases = []
        aliases.append(fact.get("condition", ""))
        aliases.extend(listify(fact.get("condition_aliases")))
        # Medicine-centered facts should also be retrievable by medicine/generic name.
        aliases.append(fact.get("medicine_name", ""))
        aliases.append(fact.get("generic_name", ""))
        aliases.extend(listify(fact.get("recommended_medicines")))
        aliases.extend(listify(fact.get("alternative_medicines")))
        aliases.extend(listify(fact.get("avoid_medicines")))
        aliases.extend(listify(fact.get("drug_class")))
        seen = set()
        out = []
        for alias in aliases:
            clean = normalize_text(alias)
            if clean and clean not in ALIAS_STOPWORDS and clean not in NON_CONDITION_ALIASES and clean not in seen:
                seen.add(clean)
                out.append(clean)
        return out

    def retrieve(
        self,
        *,
        diagnosis: str = "",
        chief_complaint: str = "",
        clinical_text: str = "",
        medication_text: str = "",
        section: str | None = None,
        limit: int = 20,
        include_context_only: bool = False,
    ) -> RetrievalResult:
        if not self.facts:
            return RetrievalResult([], [], [f"guideline RAG file not found or empty: {self.facts_path}"])
        query_text = normalize_text(" ".join([diagnosis, chief_complaint, clinical_text]))
        med_text = normalize_text(medication_text)
        candidates: dict[str, tuple[int, dict[str, Any], set[str]]] = {}
        matched_conditions = set()

        for alias, facts in self.condition_index.items():
            in_clinical = phrase_in_text(alias, query_text)
            in_medicine = phrase_in_text(alias, med_text)
            if not in_clinical and not in_medicine:
                continue
            if section in {"Dx", "Inv"} and not in_clinical and in_medicine:
                # Diagnosis/investigation retrieval must be driven by patient
                # evidence, not by medicines already prescribed. Otherwise a
                # current medicine such as amoxicillin can pull unrelated
                # diagnosis facts such as pharyngitis into a dog-bite case.
                continue
            matched_conditions.add(alias)
            for fact in facts:
                score = self._score_fact(
                    fact, alias, in_clinical, in_medicine, section, include_context_only, query_text, med_text
                )
                if score <= 0:
                    continue
                fid = fact.get("fact_id") or json.dumps(fact, ensure_ascii=False, sort_keys=True)
                if fid not in candidates or score > candidates[fid][0]:
                    candidates[fid] = (score, fact, {alias})
                else:
                    candidates[fid][2].add(alias)

        if section in {"Dx", "Inv", None}:
            self._add_textbook_symptom_candidates(candidates, query_text, section, include_context_only)

        ranked = sorted(
            candidates.values(),
            key=lambda x: (
                -x[0],
                int(x[1].get("source_priority") or 99),
                x[1].get("source_id", ""),
                x[1].get("fact_type", ""),
            ),
        )
        facts = []
        for score, fact, aliases in ranked[:limit]:
            item = dict(fact)
            item["retrieval_score"] = score
            item["matched_aliases"] = sorted(aliases)
            facts.append(item)
        warnings = []
        if not facts:
            warnings.append("no guideline facts matched exact condition/alias terms")
        return RetrievalResult(facts, sorted(matched_conditions), warnings)

    def _add_textbook_symptom_candidates(
        self,
        candidates: dict[str, tuple[int, dict[str, Any], set[str]]],
        query_text: str,
        section: str | None,
        include_context_only: bool,
    ) -> None:
        """Add symptom-to-diagnosis textbook facts by symptom overlap.

        This is deliberately conservative: it only applies to textbook Dx/Inv
        facts, requires at least two meaningful symptom/evidence token matches,
        and is scored as fallback support below exact national/WHO guideline hits.
        """
        if not query_text:
            return
        for fact in self.facts:
            if fact.get("guideline_type") != "textbook":
                continue
            if not (fact_relevant_to_section(fact, "Dx") or fact_relevant_to_section(fact, "Inv")):
                continue
            if (
                not include_context_only
                and fact.get("candidate_strength") == "context_only"
                and fact.get("fact_type")
                not in {
                    "diagnostic_clue",
                    "differential_diagnosis",
                    "red_flag",
                    "recommended_action",
                    "recommended_investigation",
                }
            ):
                continue
            overlap = symptom_overlap(query_text, fact)
            if len(overlap) < 2:
                continue
            score = 35 + min(len(overlap), 5) * 8
            if section and section in audit_uses_for_fact(fact):
                score += 20
            elif section and not fact_relevant_to_section(fact, section):
                score -= 15
            # textbook remains fallback below local/WHO exact condition hits
            if score <= 0:
                continue
            fid = fact.get("fact_id") or json.dumps(fact, ensure_ascii=False, sort_keys=True)
            aliases = {"symptom_overlap:" + ",".join(sorted(overlap))}
            if fid not in candidates or score > candidates[fid][0]:
                candidates[fid] = (score, fact, aliases)
            else:
                candidates[fid][2].update(aliases)

    def _score_fact(
        self,
        fact: dict[str, Any],
        alias: str,
        in_clinical: bool,
        in_medicine: bool,
        section: str | None,
        include_context_only: bool,
        query_text: str = "",
        med_text: str = "",
    ) -> int:
        fact_type = str(fact.get("fact_type") or "")
        rule_focus = str(fact.get("rule_focus") or "")
        if fact_type in LOW_VALUE_FACT_TYPES or rule_focus.startswith(LOW_VALUE_RULE_PREFIXES):
            return 0
        fact_category_text = " ".join(
            str(fact.get(key) or "")
            for key in (
                "condition",
                "condition_aliases",
                "fact_text",
                "rule_focus",
                "trigger_evidence",
                "recommended_advice",
                "referral_criteria",
                "admission_criteria",
            )
        )
        fact_categories = category_markers(fact_category_text)
        query_categories = category_markers(query_text)
        if fact_categories and query_categories and not (fact_categories & query_categories):
            return 0
        if not include_context_only and fact.get("candidate_strength") == "context_only":
            return 0
        score = 0
        priority = int(fact.get("source_priority") or 9)
        score += max(0, 30 - priority * 5)
        if in_clinical:
            score += 80
        if in_medicine:
            score += 55
            if section in {"Rx", "Adv"}:
                # For medicine/advice tasks, a fact tied to the actual written
                # medicine should outrank broad same-disease alternatives.
                score += 45
        condition_terms = [
            term
            for term in [fact.get("condition", ""), *listify(fact.get("condition_aliases"))]
            if normalize_text(term) not in NON_CONDITION_ALIASES
        ]
        condition_hit = any(phrase_in_text(term, query_text) for term in condition_terms)
        medicine_hit = any(
            phrase_in_text(term, med_text)
            for term in [
                fact.get("medicine_name", ""),
                fact.get("generic_name", ""),
                *listify(fact.get("recommended_medicines")),
                *listify(fact.get("alternative_medicines")),
                *listify(fact.get("avoid_medicines")),
            ]
        )
        if section in {"Rx", "Adv"} and condition_hit and medicine_hit:
            # Exact disease + exact current medicine is the most precise hit.
            score += 70
        elif section in {"Rx", "Adv"} and medicine_hit and query_text and not condition_hit:
            # Keep medicine-safety facts available, but do not let a different
            # disease's regimen outrank the matching condition regimen.
            score -= 20
        if section:
            relevant_types = SECTION_RELEVANT_FACT_TYPES.get(section, set())
            uses = audit_uses_for_fact(fact)
            if fact_type in relevant_types and section in uses:
                score += 45
            elif fact_type in relevant_types:
                score += 20
            elif section in uses:
                # Keep potentially useful but schema-imperfect rows available,
                # without letting mislabeled facts beat truly section-relevant rows.
                score += 5
            else:
                score -= 35
        if fact.get("jurisdiction") == "Bangladesh":
            score += 25
        elif fact.get("jurisdiction") == "WHO":
            score += 10
        elif fact.get("jurisdiction") == "International":
            score -= 5
        if fact.get("candidate_strength") == "strong":
            score += 15
        elif fact.get("candidate_strength") == "conditional":
            score += 8
        score += _source_condition_match_score(fact)
        # Avoid returning generic textbook facts when a same-condition local/WHO fact is likely better,
        # but do not completely suppress fallback facts.
        if fact.get("local_applicability") == "fallback_only":
            score -= 20
        return score


def medicine_query_text(raw_medication_text: str = "", normalized_medicines: list[dict[str, Any]] | None = None) -> str:
    """Build retriever medication text from raw prescription + mapped medicines.

    This is the key bridge from prescription data to medicine RAG: raw brands
    such as Ace may not appear in guideline facts, but medicine_mapper can add
    the generic name Paracetamol. We still keep the raw text for exact brand or
    alias hits.
    """
    parts = [raw_medication_text or ""]
    for med in normalized_medicines or []:
        for key in ("brand_name", "generic_name", "raw_text", "dosage_form", "strength"):
            value = med.get(key)
            if value:
                parts.append(str(value))
        for candidate in med.get("candidate_matches") or []:
            if isinstance(candidate, dict):
                for key in ("brand_name", "generic_name"):
                    if candidate.get(key):
                        parts.append(str(candidate[key]))
    return " ".join(dict.fromkeys(part.strip() for part in parts if part and part.strip()))
