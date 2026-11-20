from __future__ import annotations

import re

from .models import Finding

_STOP = {"the", "and", "for", "with", "that", "this", "from", "should", "not"}


def _tokens(finding: Finding) -> set[str]:
    return {
        word
        for word in re.findall(r"[a-z0-9]+", f"{finding.evidence} {finding.why}".lower())
        if len(word) > 2 and word not in _STOP
    }


def reconcile(findings: list[Finding], order: tuple[str, ...]) -> tuple[list[Finding], list[str]]:
    rank = {section: index for index, section in enumerate(order)}
    ordered = sorted(findings, key=lambda item: (rank.get(item.section, 99), item.concern_id))
    roots: list[tuple[Finding, set[str]]] = []
    for finding in ordered:
        tokens = _tokens(finding)
        for root, root_tokens in roots:
            union = tokens | root_tokens
            overlap = len(tokens & root_tokens) / len(union) if union else 0
            if overlap >= 0.55:
                finding.related_to = f"{root.section}:{root.concern_id}"
                finding.likely_redundant = True
                break
        else:
            roots.append((finding, tokens))
    summary = [f"{item.section} [{item.concern_id}]: {item.why}" for item, _ in roots]
    return ordered, summary
