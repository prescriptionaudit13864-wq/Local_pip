"""Interpret prescription advice into structured medication-action hints.

This module does not make audit findings. It only extracts conservative,
traceable actions from Advice text so other engines do not need scattered
phrase rules.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from .provider import ModelProvider, parse_json_value
from .text_translation import translated_text


def _text(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text.casefold() in {"", "nan", "none", "null"} else text


def _normalize(value: Any) -> str:
    text = translated_text(_text(value)).casefold()
    return re.sub(r"[^a-z0-9\s]+", " ", text)


_PRIOR_SCOPE_RE = re.compile(
    r"\b(?:previous|prior|old|earlier|existing|past)\s+"
    r"(?:medicine|medicines|medication|medications|drug|drugs|rx|prescription|regimen|treatment)s?\b",
    re.IGNORECASE,
)
_CURRENT_REGIMEN_RE = re.compile(
    r"\b(?:only|solely|just)\b(?:\s+\w+){0,8}\s+"
    r"\b(?:current|today|today's|this|new)\b(?:\s+\w+){0,8}\s+"
    r"\b(?:medicine|medicines|medication|medications|drug|drugs|rx|prescription|regimen|treatment|consultation)s?\b",
    re.IGNORECASE,
)
_PRIOR_EXCLUDED_RE = re.compile(
    r"\b(?:previous|prior|old|earlier|existing|past)\b(?:\s+\w+){0,6}\s+"
    r"\b(?:excluded|exclude|not\s+included|omitted|cancelled|canceled|stopped|discontinued|withheld|held)\b",
    re.IGNORECASE,
)
_SUPERSEDE_PRIOR_RE = re.compile(
    r"\b(?:supersede|supersedes|replace|replaces|replaced|substitute|substitutes)\b"
    r"(?:\W+\w+){0,12}\W+"
    r"\b(?:medicine|medicines|medication|medications|drug|drugs|rx|prescription|regimen|treatment|orders?)s?\b",
    re.IGNORECASE,
)
_STOP_PRIOR_RE = re.compile(
    r"\b(?:do\s*not|don't|dont|not|no|never|avoid|stop|stopped|withhold|hold|omit|skip|cease|discontinue|discontinued|cancel)\b"
    r"(?:\W+\w+){0,6}?\W+"
    r"\b(?:continue|take|use|start|restart|medicine|medicines|medication|medications|drug|drugs|rx|prescription|regimen|treatment)s?\b",
    re.IGNORECASE,
)


def interpret_advice_actions(advice: Any) -> list[dict[str, Any]]:
    """Return conservative structured actions detected in Advice text."""
    raw = _text(advice)
    if not raw:
        return []
    translated = translated_text(raw)
    actions: list[dict[str, Any]] = []

    if _STOP_PRIOR_RE.search(translated) and _PRIOR_SCOPE_RE.search(translated):
        actions.append({
            "action": "stop_prior_medicines",
            "scope": "all_prior_medicines",
            "evidence": raw,
            "confidence": "high",
        })

    if _CURRENT_REGIMEN_RE.search(translated) and (
        _PRIOR_SCOPE_RE.search(translated) or _PRIOR_EXCLUDED_RE.search(translated)
    ):
        actions.append({
            "action": "current_regimen_only",
            "scope": "all_prior_medicines",
            "evidence": raw,
            "confidence": "high",
        })

    if _PRIOR_EXCLUDED_RE.search(translated):
        actions.append({
            "action": "exclude_prior_medicines",
            "scope": "all_prior_medicines",
            "evidence": raw,
            "confidence": "high",
        })

    if _SUPERSEDE_PRIOR_RE.search(translated) and (
        _PRIOR_SCOPE_RE.search(translated)
        or re.search(r"\b(?:earlier|previous|prior|old|past)\s+visits?\b", translated, re.IGNORECASE)
        or re.search(r"\b(?:current|today|today's|today’s|this|new)\s+(?:orders?|regimen|prescription)\b", translated, re.IGNORECASE)
    ):
        actions.append({
            "action": "current_regimen_only",
            "scope": "all_prior_medicines",
            "evidence": raw,
            "confidence": "high",
        })

    return actions


_LLM_SYSTEM = (
    "You classify prescription Advice text into medication-regimen action labels. "
    "Return JSON only. Do not create audit findings. Do not discuss drug safety. "
    "Use only the supplied text. Interpret Bengali and English advice, including "
    "non-literal or polite clinical wording, but only return a replacement/stop "
    "action when the advice clearly means old/prior treatment is ended, excluded, "
    "superseded, or replaced by the current prescription."
)


def interpret_advice_actions_with_llm(
    advice: Any,
    provider: ModelProvider | None,
) -> list[dict[str, Any]]:
    """Use a small model as a semantic fallback for advice action extraction."""
    raw = _text(advice)
    if not raw or provider is None:
        return []
    prompt = {
        "task": "Classify whether this advice changes prior/old medicines.",
        "allowed_actions": [
            "current_regimen_only",
            "stop_prior_medicines",
            "exclude_prior_medicines",
            "continue_prior_medicines",
            "medicine_specific_stop",
            "unclear",
        ],
        "rules": [
            "Return current_regimen_only when advice says today's/current/new medicines/orders/regimen replace or supersede earlier/prior/old medicines.",
            "Return current_regimen_only when Bengali or English wording clearly says the previous treatment course/regimen has ended and today's prescription starts a new regimen, even if phrased politely or metaphorically.",
            "Return stop_prior_medicines when advice says not to continue prior/old medicines.",
            "Return exclude_prior_medicines when prior/old medicines are excluded/not included.",
            "Return continue_prior_medicines only when prior medicines are explicitly continued.",
            "Return unclear for vague follow-up or generic lifestyle advice.",
        ],
        "output_schema": {
            "actions": [
                {
                    "action": "one allowed action",
                    "scope": "all_prior_medicines or specific_medicine or unclear",
                    "target_text": "short target phrase if specific",
                    "evidence": "short exact source phrase",
                    "confidence": "high or medium or low",
                }
            ]
        },
        "advice": raw,
    }
    response = provider.audit(
        system=_LLM_SYSTEM,
        prompt=json.dumps(prompt, ensure_ascii=False),
        max_tokens=350,
    )
    parsed = parse_json_value(response)
    actions = parsed.get("actions", []) if isinstance(parsed, Mapping) else []
    result: list[dict[str, Any]] = []
    allowed = {
        "current_regimen_only",
        "stop_prior_medicines",
        "exclude_prior_medicines",
        "continue_prior_medicines",
        "medicine_specific_stop",
        "unclear",
    }
    for item in actions:
        if not isinstance(item, Mapping):
            continue
        action = _text(item.get("action"))
        if action not in allowed:
            continue
        result.append({
            "action": action,
            "scope": _text(item.get("scope")) or "unclear",
            "target_text": _text(item.get("target_text")),
            "evidence": _text(item.get("evidence")) or raw,
            "confidence": _text(item.get("confidence")) or "medium",
            "source": "llm_advice_action_classifier",
        })
    return result


def advice_stops_prior_medicine(
    advice: Any,
    prior_medicine: Mapping[str, Any],
    *,
    actions: list[dict[str, Any]] | None = None,
) -> bool:
    """Return true when Advice clearly removes a prior medicine from the regimen."""
    parsed = actions if actions is not None else interpret_advice_actions(advice)
    if any(
        item.get("scope") == "all_prior_medicines"
        and item.get("action") in {"stop_prior_medicines", "current_regimen_only", "exclude_prior_medicines"}
        for item in parsed
    ):
        return True

    for item in parsed:
        if item.get("action") != "medicine_specific_stop":
            continue
        target = _text(item.get("target_text") or item.get("evidence"))
        if target and (
            _contains_target(target, prior_medicine.get("raw_text", ""))
            or _contains_target(target, prior_medicine.get("generic_name", ""))
        ):
            return True

    normalized = _normalize(advice)
    stop_terms = r"\b(?:do\s*not|don't|dont|avoid|stop|withhold|hold|cease|discontinue|skip)\b"
    medicine_terms = [
        _text(prior_medicine.get("raw_text", "")),
        _text(prior_medicine.get("generic_name", "")),
    ]
    for term in medicine_terms:
        clean = re.sub(r"[^a-z0-9\s]+", " ", term.casefold()).strip()
        words = [clean] + [word for word in clean.split() if len(word) >= 5]
        if any(
            word
            and re.search(stop_terms + r"(?:\s+\w+){0,6}\s+" + re.escape(word), normalized)
            for word in words
        ):
            return True
    return False


def _contains_target(target: Any, medicine_text: Any) -> bool:
    target_clean = re.sub(r"[^a-z0-9\s]+", " ", _text(target).casefold())
    medicine_clean = re.sub(r"[^a-z0-9\s]+", " ", _text(medicine_text).casefold())
    if not target_clean or not medicine_clean:
        return False
    terms = [target_clean] + [word for word in target_clean.split() if len(word) >= 5]
    return any(term and re.search(rf"(?<!\w){re.escape(term)}(?!\w)", medicine_clean) for term in terms)


__all__ = [
    "advice_stops_prior_medicine",
    "interpret_advice_actions",
    "interpret_advice_actions_with_llm",
]
