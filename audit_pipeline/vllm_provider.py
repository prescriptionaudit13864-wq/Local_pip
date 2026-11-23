"""Both audit layers served by one vLLM server: base model loaded once, the fine-tune as a LoRA.

Select with AUDIT_PROVIDER=vllm. The audit logic is exactly LocalChatProvider's (training-format prompts, per-line
prompts, fast-concern decoding, model-first findings, the clinical reviewer's prefix and stop); only the model
calls go to the server instead of to an in-process model:

* layer 1 (fast check) = requests that name the LoRA (VLLM_LORA_NAME);
* layer 2 (clinical reviewer) = requests that name the base model (HF_MODEL), i.e. the adapter off.

vLLM picks the adapter per request, so the two layers no longer take turns on the GPU and the one-at-a-time GPU lock
of the in-process provider is not used. Prompts are rendered and tokenized here with the base model's own tokenizer
and sent as token ids, so the server sees exactly the tokens the in-process model saw (no extra BOS token, same chat
template). Greedy decoding (temperature 0), as in-process.

Settings (.env):
  VLLM_BASE_URL   default http://127.0.0.1:8000/v1
  VLLM_LORA_NAME  default prescribex  (the name given to --lora-modules when starting vLLM)
  VLLM_API_KEY    optional, if the server was started with --api-key
  VLLM_TIMEOUT    seconds per request, default 120
  HF_MODEL        the base model id; must match vLLM's --served-model-name (default: the model path it loaded)
Start the server with deploy/vllm/start_vllm.sh (WSL2).
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

from .provider import LocalChatProvider

log = logging.getLogger(__name__)

DEFAULT_VLLM_BASE_URL = "http://127.0.0.1:8000/v1"
DEFAULT_LORA_NAME = "prescribex"


class VLLMProvider(LocalChatProvider):
    """LocalChatProvider whose model calls go to a vLLM server (see module docstring)."""

    def __init__(self, *, base_url: str | None = None, model: str | None = None, lora: str | None = None) -> None:
        self.base_url = (base_url or os.getenv("VLLM_BASE_URL") or DEFAULT_VLLM_BASE_URL).strip().rstrip("/")
        self.model = (model or os.getenv("HF_MODEL") or "").strip()
        if not self.model:
            raise RuntimeError("AUDIT_PROVIDER=vllm needs HF_MODEL = the base model vLLM serves")
        self.lora = (lora or os.getenv("VLLM_LORA_NAME") or DEFAULT_LORA_NAME).strip()
        self.api_key = (os.getenv("VLLM_API_KEY") or "").strip()
        self.timeout = float(os.getenv("VLLM_TIMEOUT") or 120)
        self.last_output_character_count = 0
        self.last_response_text_preview = ""
        # client stays None: the parent then takes its batched in-process code paths, which here end in
        # _generate_raw_batch / _generate below and so in the server.
        self.client = None
        self._model = None
        token = (os.getenv("HF_TOKEN") or "").strip() or None
        # Server models cannot be inspected for training_config.json; AUDIT_PROMPT_MODE=finetune decides (as in .env).
        self._init_settings(token)
        from transformers import AutoTokenizer

        self._tokenizer = AutoTokenizer.from_pretrained(self.model, token=token)
        self.always_prefill = not getattr(self._tokenizer, "chat_template", None)
        self._check_server()
        log.info("[vllm] %s: base=%s (layer 2), lora=%s (layer 1)", self.base_url, self.model, self.lora)

    # --- server calls -------------------------------------------------------------------------------------------

    def _request(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        data = None if body is None else json.dumps(body).encode("utf-8")
        method = "GET" if body is None else "POST"
        req = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise RuntimeError(f"vLLM server error {exc.code} on {path}: {detail}") from None
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"vLLM server not reachable at {self.base_url} ({type(exc).__name__})") from None

    def _check_server(self) -> None:
        served = {str(m.get("id", "")) for m in self._request("/models").get("data", [])}
        missing = [name for name in (self.model, self.lora) if name not in served]
        if missing:
            raise RuntimeError(
                f"vLLM at {self.base_url} does not serve {missing} (it serves {sorted(served)}). Start it with "
                f"--served-model-name {self.model} --lora-modules {self.lora}=<adapter folder>."
            )

    def _complete(self, model: str, token_lists: list[list[int]], max_tokens: int, stop: str | None) -> list[str]:
        body: dict[str, Any] = {
            "model": model,
            "prompt": token_lists,
            "max_tokens": max_tokens,
            "temperature": 0,
            "skip_special_tokens": True,
        }
        if stop:
            body["stop"] = [stop]
            body["include_stop_str_in_output"] = True  # as in-process: the answer keeps the stop string
        choices = sorted(self._request("/completions", body).get("choices", []), key=lambda c: c.get("index", 0))
        if len(choices) != len(token_lists):
            raise RuntimeError(f"vLLM returned {len(choices)} answers for {len(token_lists)} prompts")
        return [str(c.get("text") or "") for c in choices]

    def _ids(self, text: str) -> list[int]:
        return list(self._tokenizer(text, add_special_tokens=False)["input_ids"])

    def _chat_text(self, messages: list[dict[str, str]]) -> str:
        try:
            return self._tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True, enable_thinking=False
            )
        except TypeError:
            return self._tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)

    # --- the parent's model calls, sent to the server -------------------------------------------------------------

    def _generate_raw_batch(self, texts: list[str], max_tokens: int, stop: str | None = None) -> list[str]:
        """Layer 1: raw training-format prompts, adapter on."""
        return self._complete(self.lora, [self._ids(t) for t in texts], max_tokens, stop)

    def _generate(self, system: str, prompt: str, max_tokens: int, prefix: str, raw: bool = False) -> str:
        """Layer 1 (adapter on), as the in-process _generate: raw text, or the chat template with an answer prefix."""
        if raw:
            text = prompt
        elif getattr(self._tokenizer, "chat_template", None):
            messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
            try:
                text = self._chat_text(messages)
            except Exception as exc:
                if "alternate" not in str(exc):
                    raise
                text = self._chat_text([{"role": "user", "content": system + "\n\n" + prompt}])
        else:
            text = system + "\n\n" + prompt + "\n\n"
        text += prefix
        return prefix + self._complete(self.lora, [self._ids(text)], max_tokens, None)[0]

    def can_review(self) -> bool:
        return True

    def review(
        self, messages: list[dict[str, str]], max_tokens: int = 300, prefix: str = '{"problems": [', stop: str = "]}"
    ) -> str:
        """Layer 2: the base model (no LoRA) in its chat format, answer started with `prefix`, stopped at `stop`."""
        text = self._chat_text(messages) + prefix
        return prefix + self._complete(self.model, [self._ids(text)], max_tokens, stop)[0]

    def audit(self, *, system: str, prompt: str, max_tokens: int) -> Any:
        # No GPU lock: the adapter is chosen per request on the server, and vLLM batches concurrent requests.
        return self._audit_locked(system=system, prompt=prompt, max_tokens=max_tokens)


__all__ = ["VLLMProvider"]
