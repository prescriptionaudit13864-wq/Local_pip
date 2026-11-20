"""Provider backed by the locally trained Gemma 4 section classifier.

The model (``prescription_audit_gemma4/.../serve_gemma4.py``) was trained to read one prescription
section and name the error types that apply. The V3 pipeline instead hands the provider a list of
rule-engine *candidates* to confirm or reject. This adapter bridges the two:

* candidates whose rule the model was trained on are confirmed only when the model flags that rule;
* candidates outside the model's vocabulary follow ``GEMMA_UNSUPPORTED`` (default ``confirm_high``:
  keep only high-evidence safety candidates, drop the rest);
* prompts that are not section adjudication (RAG router, advice classifier) raise
  :class:`UnsupportedPrompt`; their callers already fall back to deterministic behaviour.

Configuration (environment / .env): ``GEMMA_SERVER_URL`` (default http://127.0.0.1:8766),
``GEMMA_TIMEOUT`` seconds (default 180), ``GEMMA_UNSUPPORTED`` = ``confirm_high`` | ``omit`` | ``confirm``.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any

from .finetune_prompt import (
    UNSUPPORTED_POLICIES,
    UnsupportedPrompt,
    findings_from_detected,
    flatten_candidates,
    parse_section_task,
)

DEFAULT_GEMMA_URL = "http://127.0.0.1:8766"


class LocalGemmaProvider:
    """Confirms rule-engine candidates using the local Gemma 4 section classifier."""

    def __init__(self, *, url: str | None = None, timeout: float | None = None) -> None:
        self.url = (url or os.getenv("GEMMA_SERVER_URL") or DEFAULT_GEMMA_URL).strip().rstrip("/")
        self.timeout = float(timeout or os.getenv("GEMMA_TIMEOUT") or 180)
        self.unsupported_policy = (os.getenv("GEMMA_UNSUPPORTED") or "confirm_high").strip().casefold()
        if self.unsupported_policy not in UNSUPPORTED_POLICIES:
            raise RuntimeError("GEMMA_UNSUPPORTED must be confirm_high, omit, or confirm")
        self.last_output_character_count = 0
        self.last_response_text_preview = ""
        health = self._request("/health")
        self.scope: dict[str, set[str]] = {
            section: set(rules) for section, rules in (health.get("sections") or {}).items()
        }
        self.model = str(health.get("model") or "gemma4")

    # ------------------------------------------------------------------ transport
    def _request(self, path: str, body: Mapping[str, Any] | None = None) -> dict[str, Any]:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.url + path,
            data=data,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST" if data is not None else "GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Gemma server returned HTTP {exc.code} for {path}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(
                f"Local Gemma server not reachable at {self.url} ({type(exc).__name__}). "
                "Start it with: python serve_gemma4.py (in prescription_audit_gemma4)"
            ) from exc

    # ------------------------------------------------------------------ adjudication
    def audit(self, *, system: str, prompt: str, max_tokens: int) -> dict[str, Any]:
        self.last_output_character_count = 0
        self.last_response_text_preview = ""
        task = parse_section_task(prompt)
        section = str(task.get("section", "")).strip()
        candidates = flatten_candidates(task.get("candidates"))

        flagged: dict[str, Mapping[str, Any]] = {}
        in_scope = self.scope.get(section, set())
        if in_scope and candidates:
            result = self._request(
                "/classify",
                {"section": section, "context": task.get("fields_used") or {}},
            )
            if not result.get("parsed_ok", False):
                raise ValueError("local Gemma model response did not contain valid JSON")
            self.last_output_character_count = len(result.get("raw", ""))
            self.last_response_text_preview = str(result.get("raw", ""))[:1000]
            flagged = {e["rule_id"]: e for e in result.get("detected_errors", []) if "rule_id" in e}

        findings = findings_from_detected(candidates, flagged, in_scope, self.unsupported_policy)
        return {"section": section, "findings": findings}


__all__ = ["LocalGemmaProvider", "UnsupportedPrompt", "DEFAULT_GEMMA_URL"]
