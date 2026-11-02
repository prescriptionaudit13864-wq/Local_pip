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
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from .rule_text import RULE_TEXT

TAXONOMY_PATH = Path(__file__).resolve().parent / "configs" / "finetune_taxonomy.json"
UNSUPPORTED_POLICIES = {"confirm_high", "omit", "confirm"}

# Pipeline rule ids that mean the same as one or more rules the model was trained on.
RULE_ALIASES: dict[str, tuple[str, ...]] = {
    "CC.context.demo": ("CC.demo",),
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


def build_finetune_prompt(section: str, fields: Mapping[str, Any]) -> str | None:
    """The exact training prompt for one section, or None when the model was not trained on it."""
    entry = load_taxonomy().get(section)
    if not entry:
        return None
    context = {
        key: fields[key]
        for key in entry["context_keys"]
        if key in fields and fields[key] not in (None, "", [], {})
    }
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
) -> list[dict[str, Any]]:
    """Turn the classifier's flagged rules into pipeline findings, one per confirmed candidate."""
    findings = []
    for candidate in candidates:
        rule_id = str(candidate.get("rule_id", "")).strip()
        trained_ids = RULE_ALIASES.get(rule_id, (rule_id,))
        if any(item in in_scope for item in trained_ids):
            trained_id = next((item for item in trained_ids if item in flagged), None)
            if trained_id is None:
                continue
            why, correction = RULE_TEXT.get(trained_id, (flagged[trained_id].get("concern", ""), ""))
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


__all__ = [
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
