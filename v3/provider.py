from __future__ import annotations

import json
import os
import re
from typing import Any, Mapping, Protocol


_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>.*?</think>", re.IGNORECASE | re.DOTALL)


def strip_think_blocks(text: str) -> str:
    return _THINK_BLOCK_RE.sub("", text).strip()


def parse_json_value(value: Any) -> Any:
    """Return the first valid JSON object or list without echoing output on failure."""
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, list):
        return value
    if not isinstance(value, str):
        raise ValueError(f"model returned unsupported type {type(value).__name__}")

    text = strip_think_blocks(value)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        pass
    else:
        return dict(parsed) if isinstance(parsed, Mapping) else parsed

    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character not in "{[":
            continue
        try:
            candidate, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, Mapping):
            return dict(candidate)
        if isinstance(candidate, list):
            return candidate
    raise ValueError("model response did not contain valid JSON")


def parse_json_object(value: Any) -> dict[str, Any]:
    parsed = parse_json_value(value)
    if not isinstance(parsed, Mapping):
        raise ValueError("model response did not contain a valid JSON object")
    return dict(parsed)


def repair_json_object(text: str) -> dict | None:
    """Recover a JSON object from a small model's reply that is cut off or has trailing chatter.

    Keeps every complete finding and closes the structure, e.g. '{"section":"Inv","findings":[{...},{"cand' ->
    the first finding only.
    """
    text = strip_think_blocks(text)
    start = text.find("{")
    if start < 0:
        return None
    body = text[start:]
    closers = [i for i, ch in enumerate(body) if ch == "}"]
    for position in reversed(closers):
        for suffix in ("", "]}", "}", "]}}"):
            try:
                value = json.loads(body[: position + 1] + suffix)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
    return None


class ModelProvider(Protocol):
    def audit(self, *, system: str, prompt: str, max_tokens: int) -> Any: ...


class AzureOpenAIProvider:
    """Small adapter that keeps cloud SDK details outside the pipeline."""

    def __init__(self) -> None:
        from openai import AzureOpenAI

        required = {
            "AZURE_OPENAI_API_KEY": os.getenv("AZURE_OPENAI_API_KEY"),
            "AZURE_OPENAI_ENDPOINT": os.getenv("AZURE_OPENAI_ENDPOINT"),
            "AZURE_API_VERSION": os.getenv("AZURE_API_VERSION"),
            "AZURE_DEPLOYMENT": os.getenv("AZURE_DEPLOYMENT"),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise RuntimeError(f"Missing Azure configuration: {', '.join(missing)}")
        self.deployment = required["AZURE_DEPLOYMENT"]
        self.last_output_character_count = 0
        self.last_response_text_preview = ""
        self.client = AzureOpenAI(
            api_key=required["AZURE_OPENAI_API_KEY"],
            azure_endpoint=required["AZURE_OPENAI_ENDPOINT"],
            api_version=required["AZURE_API_VERSION"],
        )

    def audit(self, *, system: str, prompt: str, max_tokens: int) -> dict:
        self.last_output_character_count = 0
        self.last_response_text_preview = ""
        response = self.client.chat.completions.create(
            model=self.deployment,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=max_tokens,
        )
        content = response.choices[0].message.content or "{}"
        self.last_output_character_count = len(content)
        self.last_response_text_preview = content[:1000]
        return parse_json_object(content)



DEFAULT_GROQ_MODEL = "llama-3.1-8b-instant"
GROQ_OPENAI_BASE_URL = "https://api.groq.com/openai/v1"


class GroqProvider:
    """Groq adapter using its OpenAI-compatible chat completions endpoint."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        configured_key = (api_key or os.getenv("GROQ_API_KEY") or "").strip()
        if not configured_key:
            raise RuntimeError("Missing Groq configuration: GROQ_API_KEY")

        from openai import OpenAI
        self.model = (model or os.getenv("GROQ_MODEL") or DEFAULT_GROQ_MODEL).strip()
        if not self.model:
            self.model = DEFAULT_GROQ_MODEL
        self.last_output_character_count = 0
        self.last_response_text_preview = ""
        self.client = OpenAI(
            api_key=configured_key,
            base_url=GROQ_OPENAI_BASE_URL,
        )

    def audit(self, *, system: str, prompt: str, max_tokens: int) -> Any:
        self.last_output_character_count = 0
        self.last_response_text_preview = ""
        request = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
            "max_tokens": max_tokens,
        }
        if self.model.casefold().startswith("qwen/qwen3"):
            request["reasoning_effort"] = "none"
        response = self.client.chat.completions.create(**request)
        content = response.choices[0].message.content or "{}"
        self.last_output_character_count = len(content)
        self.last_response_text_preview = content[:1000]
        return parse_json_value(content)




class LocalChatProvider:
    """A Hugging Face model run in this process, or any OpenAI-compatible server (Ollama, LM Studio, vLLM...).

    Select with AUDIT_PROVIDER=local. Settings (.env): HF_MODEL (model id), HF_ADAPTER, HF_QUANT, HF_TOKEN;
    set LLM_BASE_URL (and LLM_API_KEY) only to use an external server instead of loading in-process.
    """

    def __init__(self, *, base_url: str | None = None, model: str | None = None) -> None:
        self.base_url = (base_url or os.getenv("LLM_BASE_URL") or "").strip()
        self.model = (model or os.getenv("LLM_MODEL") or os.getenv("HF_MODEL") or "local-model").strip()
        self.last_output_character_count = 0
        self.last_response_text_preview = ""
        self.always_prefill = False
        self.client = None
        self._tokenizer = self._model = None
        if not self.base_url:
            # No server configured: download HF_MODEL from Hugging Face (first run) and run it in this process.
            self._tokenizer, self._model = self._load_in_process()
            self.always_prefill = not getattr(self._tokenizer, "chat_template", None)
            if self.always_prefill:
                print(f"WARNING: {self.model} has no chat template (base model). Use the -Instruct version "
                      "for reliable audits.", flush=True)
            return

        from openai import OpenAI

        self.client = OpenAI(
            api_key=os.getenv("LLM_API_KEY") or "local",
            base_url=self.base_url,
            timeout=float(os.getenv("LLM_TIMEOUT") or 300),
            max_retries=0,
        )
        try:
            self.client.models.list()
        except Exception as exc:
            raise RuntimeError(
                f"Local model server not reachable at {self.base_url} ({type(exc).__name__}). "
                "Start Ollama / LM Studio, or remove LLM_BASE_URL to run HF_MODEL in-process."
            ) from exc

    _loaded: dict = {}

    def _load_in_process(self) -> tuple:
        adapter = (os.getenv("HF_ADAPTER") or "").strip()
        quant = (os.getenv("HF_QUANT") or "none").strip().casefold()
        if quant not in {"none", "8bit", "4bit"}:
            raise RuntimeError(f"Unsupported HF_QUANT {quant!r}; expected none, 8bit or 4bit")
        key = (self.model, adapter, quant)
        if key in self._loaded:
            return self._loaded[key]
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        token = (os.getenv("HF_TOKEN") or "").strip() or None
        kwargs: dict[str, Any] = {"token": token, "device_map": "auto"}
        if quant == "none":
            kwargs["dtype"] = "auto"
        else:
            from transformers import BitsAndBytesConfig

            kwargs["quantization_config"] = (
                BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_compute_dtype=torch.bfloat16)
                if quant == "4bit" else BitsAndBytesConfig(load_in_8bit=True)
            )
        print(f"[local] loading {self.model} (downloads on first use)...", flush=True)
        tokenizer = AutoTokenizer.from_pretrained(self.model, token=token)
        model = AutoModelForCausalLM.from_pretrained(self.model, **kwargs)
        if adapter:
            from peft import PeftModel

            model = PeftModel.from_pretrained(model, adapter, token=token)
        model.eval()
        self._loaded[key] = (tokenizer, model)
        return self._loaded[key]

    def _generate(self, system: str, prompt: str, max_tokens: int, prefix: str) -> str:
        import torch

        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        if getattr(self._tokenizer, "chat_template", None):
            def render(msgs: list) -> str:
                try:
                    return self._tokenizer.apply_chat_template(
                        msgs, tokenize=False, add_generation_prompt=True, enable_thinking=False)
                except TypeError:
                    return self._tokenizer.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)

            try:
                text = render(messages)
            except Exception as exc:
                if "alternate" not in str(exc):
                    raise
                # Templates such as Mistral's reject a system role: fold it into the user turn.
                text = render([{"role": "user", "content": system + "\n\n" + prompt}])
        else:
            text = system + "\n\n" + prompt + "\n\n"
        text += prefix
        inputs = self._tokenizer(text, return_tensors="pt", add_special_tokens=False).to(self._model.device)
        with torch.inference_mode():
            output = self._model.generate(**inputs, max_new_tokens=max_tokens, do_sample=False)
        return prefix + self._tokenizer.decode(output[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)

    def audit(self, *, system: str, prompt: str, max_tokens: int) -> Any:
        self.last_output_character_count = 0
        self.last_response_text_preview = ""
        section = re.search(r'REQUIRED_JSON_SKELETON=\{"section":"([A-Za-z]+)"', prompt)
        opening = f'{{"section":"{section.group(1)}","findings":[' if section else ""

        def ask(prefix: str) -> Any:
            # `continue_prefix` is honoured by the in-process path; external servers ignore it.
            if self.client is None:
                content = self._generate(system, prompt, max_tokens, prefix) or "{}"
            else:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                    temperature=0,
                    max_tokens=max_tokens,
                    extra_body={"continue_prefix": prefix} if prefix else {},
                )
                content = response.choices[0].message.content or "{}"
            self.last_output_character_count = len(content)
            self.last_response_text_preview = content[:1000]
            try:
                return parse_json_value(content)
            except ValueError:
                repaired = repair_json_object(content)
                if repaired is None:
                    raise
                return repaired

        # A plain reply first: forcing the JSON opening makes weak models answer "no findings" too readily.
        # Only models without a chat template (base models) always get the opening; any other model gets
        # it as a second try when its first reply was not JSON.
        if self.always_prefill and opening:
            return ask(opening)
        try:
            return ask("")
        except ValueError:
            if not opening:
                raise
        try:
            return ask(opening)
        except ValueError:
            raise ValueError(
                f"model response did not contain valid JSON; {self.model} may be a base or too small model "
                "(use an Instruct/chat version)"
            ) from None


def create_provider(name: str | None = None) -> ModelProvider:
    """Create the configured provider without exposing credential values."""
    selected = (
        name
        or os.getenv("AUDIT_PROVIDER")
        or os.getenv("LLM_PROVIDER")
        or "azure"
    ).strip().casefold()
    if selected == "azure":
        return AzureOpenAIProvider()
    if selected == "groq":
        return GroqProvider()
    if selected == "gemma":  # fine-tuned section classifier served by serve_gemma4.py
        from .gemma_provider import LocalGemmaProvider

        return LocalGemmaProvider()
    if selected in {"local", "hf"}:  # any HF chat model run in-process, or Ollama / LM Studio / vLLM via LLM_BASE_URL
        return LocalChatProvider()
    raise RuntimeError(
        f"Unsupported audit provider {selected!r}; expected azure, groq, gemma or local"
    )


__all__ = [
    "AzureOpenAIProvider",
    "DEFAULT_GROQ_MODEL",
    "GROQ_OPENAI_BASE_URL",
    "GroqProvider",
    "LocalChatProvider",
    "ModelProvider",
    "parse_json_object",
    "repair_json_object",
    "parse_json_value",
    "strip_think_blocks",
    "create_provider",
]
