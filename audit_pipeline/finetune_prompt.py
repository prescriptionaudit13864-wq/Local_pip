"""Bridge between the V3 candidate prompts and the fine-tuned section classifiers.

The fine-tuned models (LLM_Trainer ``train.py``) learned a different task from the one the V3 pipeline
asks: given one prescription section in an Alpaca-style prompt
(``### Instruction / ### Input / ### Output``) they name every error type from a fixed per-section
list and answer ``{"has_error", "section", "detected_errors": [{rule_id, rule_name, concern}]}``.
The pipeline instead sends rule-engine *candidates* to confirm or reject. This module:

* rebuilds the exact training prompt for a section from the pipeline's TASK_JSON
  (taxonomy and context keys come from ``configs/finetune_taxonomy.json``, extracted from the
  training data so the wording matches what the model saw);
* maps the model's ``detected_errors`` back onto candidates: a candidate whose rule the model was
  trained on is confirmed only when the model flags that rule (directly or via ``RULE_ALIASES``);
  candidates outside the model's vocabulary follow the unsupported policy
  (``confirm_high`` keeps only high-evidence candidates, ``omit`` drops them, ``confirm`` keeps all).

Used by the in-process ``LocalChatProvider`` (fine-tune prompt mode) and the served ``LocalGemmaProvider``.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from .rule_text import RULE_TEXT

TAXONOMY_PATH = Path(__file__).resolve().parent / "configs" / "finetune_taxonomy.json"
UNSUPPORTED_POLICIES = {"confirm_high", "omit", "confirm"}

# Pipeline rule ids that mean the same as one or more rules the model was trained on.
RULE_ALIASES: dict[str, tuple[str, ...]] = {
    "CC.context.demo": ("CC.demo",),
    "CC.time.plausibility": ("CC.time",),
    "Dx.context.demo": ("Dx.demo",),
    "Inv.relevance": ("Inv.cc", "Inv.hs"),
    "Rx.indication.missing": ("Rx.cc.mis",),
    "Rx.omission": ("Rx.cc.sym", "Rx.hs", "Rx.multi"),
    "Rx.dup.class": ("Rx.dup",),
    "Rx.dose.weight": ("Rx.demo",),
    "Rx.risk.pediatric": ("Rx.demo",),
    "Rx.risk.elderly": ("Rx.demo",),
    "Rx.freq.missing": ("Rx.instr",),
    "Rx.duration.missing": ("Rx.instr",),
    "Adv.missing": ("Adv.cc",),
    "Adv.rag.missing": ("Adv.cc",),
    "Adv.preg.danger": ("Adv.precau", "Adv.refer"),
}


class UnsupportedPrompt(RuntimeError):
    """The prompt is not a section adjudication a fine-tuned classifier can answer."""


# Sections whose training rows leave an empty field out instead of writing "": the synthetic Hx and CC rows
# (the gold CC rows never have an empty field, so they match either way).
_DROP_EMPTY_SECTIONS = {"Hx", "CC"}


@lru_cache(maxsize=1)
def load_taxonomy() -> dict[str, dict[str, Any]]:
    with open(TAXONOMY_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def trained_rule_ids(section: str) -> set[str]:
    entry = load_taxonomy().get(section) or {}
    return {str(item["rule_id"]) for item in entry.get("allowed_error_types", [])}


def parse_section_task(prompt: str) -> dict[str, Any]:
    """Return the TASK_JSON of a section audit prompt, or raise UnsupportedPrompt."""
    if "TASK_JSON=" not in prompt or "REQUIRED_JSON_SKELETON=" not in prompt:
        raise UnsupportedPrompt("fine-tuned section classifier only adjudicates section audit tasks")
    task = json.loads(prompt.split("TASK_JSON=", 1)[1])
    if not isinstance(task, dict):
        raise UnsupportedPrompt("TASK_JSON is not an object")
    return task


def flatten_candidates(candidates: Any) -> list[Mapping[str, Any]]:
    """Undo audit_writer's prompt grouping ({"evidence", "checks": [...]}) back to one dict per candidate."""
    flat: list[Mapping[str, Any]] = []
    for item in candidates or []:
        if not isinstance(item, Mapping):
            continue
        checks = item.get("checks")
        if isinstance(checks, list):
            shared = {"section": item.get("section"), "evidence": item.get("evidence")}
            flat.extend({**shared, **check} for check in checks if isinstance(check, Mapping))
        else:
            flat.append(item)
    return flat


@lru_cache(maxsize=1)
def _mapper():
    from .medicine_mapper import MedicineMapper

    data = Path(__file__).resolve().parent / "data"
    return MedicineMapper(data / "medicine.csv", data / "generic.csv")


def _drug_facts(fields: Mapping[str, Any]) -> str:
    """The Drug_Facts input exactly as LLM_Trainer/build_v6_drugfacts_data.py builds it for training."""
    from .drug_facts import drug_facts_text

    record = " ".join(str(fields.get(k, "") or "") for k in ("Chief_Complaint", "Diagnosis", "Advice", "ANC_Notes"))
    return drug_facts_text(_mapper().map(fields.get("Medication", "") or ""), age=fields.get("Age"),
                           gender=fields.get("Gender"), record_text=record)


def build_finetune_prompt(section: str, fields: Mapping[str, Any]) -> str | None:
    """The exact training prompt for one section, or None when the model was not trained on it."""
    entry = load_taxonomy().get(section)
    if not entry:
        return None
    # The physician gold rows (the last training stage) list every context key, with "" for an empty field,
    # in context_keys order. Sending only the filled fields, or extra keys, raised false alarms by about half
    # on the gold validation rows (LLM_Trainer/eval_format_gap.py, Nov 2026).
    keep_empty = section not in _DROP_EMPTY_SECTIONS
    if "Drug_Facts" in entry["context_keys"] and not fields.get("Drug_Facts"):
        fields = {**fields, "Drug_Facts": _drug_facts(fields)}
    context = {}
    for key in entry["context_keys"]:
        value = fields.get(key)
        if value in (None, "", [], {}):
            if keep_empty:
                context[key] = ""
        else:
            # Every training value is a string ("Age": "31"); a number or list from another caller would not match.
            context[key] = value if isinstance(value, str) else (
                str(value) if isinstance(value, (int, float, bool)) else json.dumps(value, ensure_ascii=False))
    model_input = {
        "section": section,
        "section_name": entry["section_name"],
        "allowed_error_types": entry["allowed_error_types"],
        "prescription_context": context,
    }
    # Same layout as LLM_Trainer/train.py build_prompt(): dicts are written with json.dumps(ensure_ascii=False).
    return (
        f"### Instruction:\n{entry['instruction']}\n\n"
        f"### Input:\n{json.dumps(model_input, ensure_ascii=False)}\n\n"
        "### Output:\n"
    )


def detected_rule_map(payload: Any) -> dict[str, Mapping[str, Any]]:
    """Index the model's detected_errors by rule_id."""
    if not isinstance(payload, Mapping):
        return {}
    detected = payload.get("detected_errors")
    if not isinstance(detected, list):
        detected = payload.get("findings")
    if not isinstance(detected, list):
        return {}
    return {
        str(item["rule_id"]).strip(): item
        for item in detected
        if isinstance(item, Mapping) and item.get("rule_id")
    }


def keep_unsupported(candidate: Mapping[str, Any], policy: str) -> bool:
    if policy == "confirm":
        return True
    if policy == "omit":
        return False
    return candidate.get("evidence_strength") == "high"


def findings_from_detected(
    candidates: list[Mapping[str, Any]],
    flagged: Mapping[str, Mapping[str, Any]],
    in_scope: set[str],
    policy: str,
    engine_confirmed: set[str] | frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Turn the classifier's flagged rules into pipeline findings, one per confirmed candidate.

    A candidate of a rule in ``engine_confirmed`` is a finding even when the model did not flag that rule.
    """
    findings = []
    for candidate in candidates:
        rule_id = str(candidate.get("rule_id", "")).strip()
        trained_ids = RULE_ALIASES.get(rule_id, (rule_id,))
        if any(item in in_scope for item in trained_ids):
            trained_id = next((item for item in trained_ids if item in flagged), None)
            if trained_id is None:
                trained_id = next((item for item in trained_ids if item in engine_confirmed), None)
                if trained_id is None:
                    continue
                why, correction = RULE_TEXT.get(trained_id, ("", ""))
                why = why or str(candidate.get("why_candidate", "") or "")
            else:
                why, correction = RULE_TEXT.get(trained_id, (flagged[trained_id].get("concern", ""), ""))
                # The model's own sentence about this prescription (taxonomy v7 on), else the rule's standard one.
                why = str(flagged[trained_id].get("why", "") or "").strip() or why
            confidence = "medium"
        else:
            if not keep_unsupported(candidate, policy):
                continue
            why = str(candidate.get("why_candidate", "") or "")
            correction = ""
            confidence = "high" if candidate.get("evidence_strength") == "high" else "medium"
        findings.append(
            {
                "candidate_id": candidate.get("candidate_id", ""),
                "rule_name": candidate.get("rule_name", ""),
                "rule_id": rule_id,
                "why": why,
                "correction": correction,
                "evidence": candidate.get("evidence", ""),
                "confidence": confidence,
            }
        )
    return findings


# Checks the fine-tune was never taught (severity and site, because the physician gold labels do not flag them)
# are asked as one plain YES/NO question per candidate, so the answer comes from the model's general medical
# knowledge. YES always means the check found a problem. Keyed by pipeline rule_id: (question template, correction shown when confirmed).
# Tried and rejected: spelling out "Reply PROBLEM if ... / Reply OK if ..." made the fine-tune copy the first
# option line back instead of judging, which flagged correct complaints.
KNOWLEDGE_QUESTIONS: dict[str, tuple[str, str]] = {
    "Hx.term.unclear": (
        "Past history entry: '{entry}'. Is this entry an unrecognizable, misspelled, or non-existent medical "
        "term? Common shorthand such as DM, HTN, BA, IHD and Bengali or Banglish words are acceptable.",
        "Clarify or correct the past-history term so the condition is unambiguous.",
    ),
    "Hx.context.demo": (
        "Patient: {patient}. Past history entry: '{entry}'. Is this past history impossible or implausible "
        "for a patient of this sex and age?",
        "Verify the patient's sex/age or correct the past-history entry.",
    ),
    "Hx.detail": (
        "Past history entry: '{entry}'. Is a detail missing that a doctor needs for safe treatment now, such as "
        "the allergen or reaction for an allergy, the operation name for a surgery, or the type of a vague "
        "condition like 'heart disease'?",
        "Document the missing detail (allergen and reaction, procedure, type or status) in the past history.",
    ),
    "Hx.time.plausibility": (
        "Patient: {patient}. Past history entry: '{entry}'. Is the stated timing impossible or unclear for "
        "this patient, for example a duration longer than the patient's age or an onset in the future?",
        "Verify and correct the onset or duration recorded in the past history.",
    ),
    "Hx.context.conflict": (
        "Patient: {patient}. {entry}. Does the past history directly contradict the current findings, after "
        "allowing for conditions that are resolved, treated, or intermittent?",
        "Reconcile the past history with the current clinical findings.",
    ),
    "Hx.duplicate": (
        "Past history entries: {entry}. Do two of these entries describe the same condition under different "
        "names, or state one condition as both present and absent?",
        "Merge the duplicate past-history entries or resolve the conflicting status.",
    ),
    "CC.1": (
        "Chief complaint entry: '{entry}'. Does this entry fail to describe any symptom, health problem, or reason "
        "for the visit (for example it only describes a normal everyday activity)?",
        "Clarify and document the patient's actual symptom or reason for consultation.",
    ),
    "CC.term.unclear": (
        "Chief complaint entry: '{entry}'. Is this entry unrecognizable, misspelled, or not a real symptom term? "
        "Understandable Bengali, Banglish, and common shorthand are acceptable.",
        "Clarify or correct the complaint wording.",
    ),
    "CC.2": (
        "Chief complaint: '{entry}'. Is the severity, grade, pattern, character, or measured value missing when a "
        "doctor needs it to judge this complaint (for example how high a fever is, or what type of pain)?",
        "Document the severity, pattern, or character of the complaint.",
    ),
    "CC.3": (
        "Chief complaint: '{entry}'. Is a clear duration or onset missing? Vague words such as 'recent' or "
        "'for some time' do not count as a duration.",
        "Document how long the complaint has been present.",
    ),
    "CC.4": (
        "Chief complaint: '{entry}'. Is the body site or side (right, left, both) missing when it matters for this "
        "complaint, for example a swelling or pain in a paired organ such as the breast, eye, or kidney?",
        "Document the site and side of the complaint.",
    ),
    "CC.context.demo": (
        "Patient: {patient}. Chief complaint: '{entry}'. Is this complaint impossible or implausible for a patient "
        "of this sex and age?",
        "Verify the patient's sex/age or correct the complaint.",
    ),
    "CC.time.plausibility": (
        "Chief complaint: '{entry}'. Is the stated duration medically impossible or implausible for this "
        "complaint?",
        "Verify and correct the stated duration of the complaint.",
    ),
    "CC.context.conflict": (
        "Patient: {patient}. {entry}. Do any of these complaints directly contradict each other or the obstetric "
        "notes? Associated states that normally occur together are not a contradiction.",
        "Reconcile the contradictory complaint entries.",
    ),
    "CC.hs": (
        "Patient: {patient}. {entry}. Does the measured health screening directly contradict the complaint, after "
        "allowing for intermittent symptoms and treatment?",
        "Reconcile the complaint with the recorded screening findings.",
    ),
}
_EVIDENCE_LABELS = ("Past history: ", "Entered chief complaint: ", "Complaint: ")
_ANSWER_FORMAT = "Answer YES or NO first, then one short sentence explaining why."


def candidate_line(candidate: Mapping[str, Any]) -> str:
    """The one entered complaint or history line a candidate is about, or "" when it is not about one line."""
    evidence = str(candidate.get("evidence", "") or "").split(" | ", 1)[0].strip()
    for label in _EVIDENCE_LABELS:
        if evidence.startswith(label):
            line = evidence.removeprefix(label).strip()
            return "" if any(other in line for other in _EVIDENCE_LABELS) else line  # several lines merged
    return ""


def build_knowledge_question(candidate: Mapping[str, Any], fields: Mapping[str, Any]) -> str | None:
    """A plain YES/NO question for one candidate check, or None when no question is defined for its rule."""
    template = KNOWLEDGE_QUESTIONS.get(str(candidate.get("rule_id", "")).strip())
    if template is None:
        return None
    evidence = str(candidate.get("evidence", "") or "").split(" | Normalized language hint:", 1)[0]
    entry = next((evidence.removeprefix(label) for label in _EVIDENCE_LABELS if evidence.startswith(label)), evidence)
    entry = entry.strip()
    age, gender = str(fields.get("Age", "") or "").strip(), str(fields.get("Gender", "") or "").strip()
    sex = {"m": "male", "f": "female"}.get(gender[:1].casefold(), "patient")
    patient = " ".join(part for part in (f"{age}-year-old" if age else "", sex) if part)
    return template[0].format(entry=entry, patient=patient) + " " + _ANSWER_FORMAT


def _echoes_question(why: str, question: str) -> bool:
    """True when the reason is mostly the question's own words rather than an explanation."""
    words = set(re.findall(r"\w+", why.casefold()))
    return not words or len(words & set(re.findall(r"\w+", question.casefold()))) / len(words) >= 0.7


def finding_from_answer(candidate: Mapping[str, Any], answer: str, question: str = "") -> dict[str, Any] | None:
    """Turn a YES answer into a pipeline finding; NO or an unreadable answer confirms nothing."""
    match = re.match(r"\W*(yes|no)\b[\s,.:;!-]*(.*)", answer.strip(), re.IGNORECASE | re.DOTALL)
    if not match or match.group(1).casefold() != "yes":
        return None
    why = match.group(2).split("\n", 1)[0].strip()
    if "answer yes or no" in why.casefold() or _echoes_question(why, question):   # no explanation of its own
        why = ""
    rule_id = str(candidate.get("rule_id", "")).strip()
    return {
        "candidate_id": candidate.get("candidate_id", ""),
        "rule_name": candidate.get("rule_name", ""),
        "rule_id": rule_id,
        "why": why or RULE_TEXT.get(rule_id, ("This entry needs review for this check.", ""))[0],
        "correction": KNOWLEDGE_QUESTIONS[rule_id][1],
        "evidence": candidate.get("evidence", ""),
        "confidence": "medium",
    }


__all__ = [
    "KNOWLEDGE_QUESTIONS",
    "candidate_line",
    "build_knowledge_question",
    "finding_from_answer",
    "RULE_ALIASES",
    "UNSUPPORTED_POLICIES",
    "UnsupportedPrompt",
    "build_finetune_prompt",
    "detected_rule_map",
    "findings_from_detected",
    "flatten_candidates",
    "keep_unsupported",
    "load_taxonomy",
    "parse_section_task",
    "trained_rule_ids",
]
