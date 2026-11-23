from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from .finetune_prompt import (
    RULE_ALIASES,
    UNSUPPORTED_POLICIES,
    build_finetune_prompt,
    build_knowledge_question,
    detected_rule_map,
    finding_from_answer,
    findings_from_detected,
    flatten_candidates,
    load_taxonomy,
    parse_section_task,
    trained_rule_ids,
)
from .finetune_prompt import (
    candidate_line as _candidate_line,
)
from .rule_text import RULE_TEXT

log = logging.getLogger(__name__)

_KNOWLEDGE_SYSTEM = "You are a careful clinical documentation reviewer."
DEFAULT_QUESTION_RULES = "CC.2,CC.4,CC.term.unclear,CC.context.conflict,Hx.term.unclear,Hx.context.conflict"
DEFAULT_ENGINE_CONFIRMED = (
    ""  # the model judges every rule; e.g. "CC.3,Rx.timing" lets the rule engine confirm those alone
)
# Sections whose candidates name one entered line, and the field that holds those lines.
_LINE_FIELDS = {"CC": "Chief_Complaint", "Hx": "Past_History"}
_LINE_LABELS = {"CC": "Complaint: ", "Hx": "Past history: "}
# The field a model-first finding points at when the model names no single line.
_SECTION_FIELD = {"Dx": "Diagnosis", "Inv": "Investigation", "Rx": "Medication", "Adv": "Advice", "Demo": "Age"}


def _trained_rule(rule_id: Any) -> str:
    rule = str(rule_id or "").strip()
    return RULE_ALIASES.get(rule, (rule,))[0]


def _model_finding(
    section: str, rule: str, entry: Mapping[str, Any], why: str, correction: str, evidence: str
) -> dict[str, Any]:
    return {
        "candidate_id": f"model:{rule}",
        "rule_name": str(entry.get("rule_name", "") or ""),
        "rule_id": rule,
        "why": why or str(entry.get("concern", "") or ""),
        "correction": correction,
        "evidence": evidence,
        "confidence": "medium",
        "model_first": True,
    }


# Where a trained answer starts copying a rule description (json.dumps layout, as in training).
_CONCERN_OPEN = '"concern": "'
_FAST_ROUND_TOKENS = 140  # cap per fast-answer round (see _answer_finetune); room for a "why" sentence
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


def _is_cached_locally(model: str) -> bool:
    """True if `model` is already in the local Hugging Face cache (no download needed)."""
    try:
        from huggingface_hub import scan_cache_dir

        return any(repo.repo_id == model for repo in scan_cache_dir().repos)
    except Exception:
        return False


def _resolve_quant(model: str, token: str | None) -> tuple[str, str]:
    """Pick the quantization to load `model` with.

    HF_QUANT may be set to none / 4bit / 8bit to force a choice. Left unset (or "auto"), it is detected
    automatically: a repo that is already quantized on the Hub (bnb-4bit, GPTQ, AWQ, QAT...) loads as published
    ("none"); anything else is quantized to 4-bit at load time, since that is what this GPU is sized for. This is
    what lets you paste any Hugging Face model id into HF_MODEL and have it load without hand-tuning HF_QUANT.
    """
    configured = (os.getenv("HF_QUANT") or "auto").strip().casefold()
    if configured in {"none", "8bit", "4bit"}:
        return configured, "set by HF_QUANT in .env"
    if configured != "auto":
        raise RuntimeError(f"Unsupported HF_QUANT {configured!r}; expected auto, none, 8bit or 4bit")
    from transformers import AutoConfig

    try:
        config = AutoConfig.from_pretrained(model, token=token)
        quant_cfg = getattr(config, "quantization_config", None)
    except Exception:
        quant_cfg = None
    if quant_cfg is None:
        return "4bit", "auto-detected: not pre-quantized on the Hub"
    method = quant_cfg.get("quant_method") if isinstance(quant_cfg, Mapping) else getattr(quant_cfg, "quant_method", "")
    method = str(getattr(method, "value", method) or "quantized")
    return "none", f"auto-detected: already {method} on the Hub"


def _resolve_prompt_mode(model: str, base_url: str, token: str | None) -> tuple[str, str]:
    """Pick how section prompts are sent: "standard" (chat + candidates) or "finetune" (training format).

    AUDIT_PROMPT_MODE may be set to standard / finetune to force a choice. Left unset (or "auto"), a model
    run in-process is detected as a fine-tune when its repo or folder carries the training_config.json that
    LLM_Trainer/train.py writes with an "### Instruction" prompt_format. Server models cannot be inspected,
    so they stay "standard" unless AUDIT_PROMPT_MODE=finetune.
    """
    configured = (os.getenv("AUDIT_PROMPT_MODE") or "auto").strip().casefold()
    if configured in {"standard", "finetune"}:
        return configured, "set by AUDIT_PROMPT_MODE in .env"
    if configured != "auto":
        raise RuntimeError(f"Unsupported AUDIT_PROMPT_MODE {configured!r}; expected auto, standard or finetune")
    if base_url:
        return "standard", "auto: server model, set AUDIT_PROMPT_MODE=finetune for a fine-tune"
    try:
        local = Path(model) / "training_config.json"
        if local.is_file():
            path = str(local)
        else:
            from huggingface_hub import hf_hub_download

            path = hf_hub_download(model, "training_config.json", token=token)
        with open(path, encoding="utf-8") as handle:
            prompt_format = str(json.load(handle).get("prompt_format", ""))
    except Exception:
        return "standard", "auto-detected: no training_config.json"
    if "### Instruction" in prompt_format:
        return "finetune", "auto-detected: training_config.json uses the ### Instruction format"
    return "standard", "auto-detected: training_config.json has another prompt format"


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
        config = {name: str(value) for name, value in required.items()}
        self.deployment = config["AZURE_DEPLOYMENT"]
        self.last_output_character_count = 0
        self.last_response_text_preview = ""
        self.client = AzureOpenAI(
            api_key=config["AZURE_OPENAI_API_KEY"],
            azure_endpoint=config["AZURE_OPENAI_ENDPOINT"],
            api_version=config["AZURE_API_VERSION"],
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
        token = (os.getenv("HF_TOKEN") or "").strip() or None
        self.prompt_mode, mode_reason = _resolve_prompt_mode(self.model, self.base_url, token)
        self.unsupported_policy = (
            (os.getenv("FINETUNE_UNSUPPORTED") or os.getenv("GEMMA_UNSUPPORTED") or "confirm_high").strip().casefold()
        )
        if self.unsupported_policy not in UNSUPPORTED_POLICIES:
            raise RuntimeError("FINETUNE_UNSUPPORTED must be confirm_high, omit, or confirm")
        # Checks the fine-tune was never taught: in finetune mode each of these is also asked as a plain YES/NO
        # question, answered from the model's own medical knowledge. Comma-separated; empty turns it off.
        self.question_rules = {
            item.strip()
            for item in os.getenv("FINETUNE_QUESTION_RULES", DEFAULT_QUESTION_RULES).split(",")
            if item.strip()
        }
        # Rules whose rule-engine candidate is already the finding (a complaint line with no duration, a medicine
        # with no meal timing): confirmed without waiting for the model, which answers per section and can leave
        # one out when it reports another rule. Comma-separated trained rule ids; "none" turns it off.
        self.engine_confirmed = {
            item.strip()
            for item in os.getenv("FINETUNE_ENGINE_CONFIRMED", DEFAULT_ENGINE_CONFIRMED).split(",")
            if item.strip() and item.strip().casefold() != "none"
        }
        # Write known concern text instead of decoding it (see _answer_finetune). Checked against normal decoding
        # on 18 validation rows: same rules on all, about 40% less time (benchmark/fast_concerns_check.py).
        # FINETUNE_FAST_CONCERNS=0 turns it off.
        self.fast_concerns = (os.getenv("FINETUNE_FAST_CONCERNS") or "1").strip().casefold() not in {
            "0",
            "false",
            "no",
            "off",
        }
        # Model-first: a rule the model flags is reported even when no rule-engine check proposed it
        # (see _model_first_findings). FINETUNE_MODEL_FIRST=0 restores "the model only confirms candidates".
        self.model_first = (os.getenv("FINETUNE_MODEL_FIRST") or "1").strip().casefold() not in {
            "0",
            "false",
            "no",
            "off",
        }
        # Line answers count on their own: a rule the model flags when asked about one complaint or history line
        # alone is reported even if its answer for the whole section left that rule out. With several lines the
        # section answer can stop at the first problem ("Pregnancy for 3 days" in a man) and miss another
        # ("Fever" with no duration), while the line's own answer flags it (Nov 2026, both engines).
        # FINETUNE_LINE_ANSWERS_ADD=0 restores "a line answer only confirms what the section answer flagged".
        self.line_answers_add = (os.getenv("FINETUNE_LINE_ANSWERS_ADD") or "1").strip().casefold() not in {
            "0",
            "false",
            "no",
            "off",
        }
        # Rules the model may not raise on its own because the live test showed it cannot judge them yet. Rx.timing:
        # the training medicines never show meal timing (it is not in the exported data), so the model flags
        # timing even on "[After meal]" medicines (3 false alarms in benchmark/live_suite, Nov 2026). Remove a
        # rule from this list once a model trained on examples with written timing passes the live test.
        self.model_first_exclude = {
            item.strip()
            for item in (os.getenv("FINETUNE_MODEL_FIRST_EXCLUDE") or "Rx.timing").split(",")
            if item.strip() and item.strip().casefold() != "none"
        }
        self.fast_fallbacks = 0  # answers the fast path handed back to normal decoding
        log.info("[local] %s: prompt_mode=%s (%s)", self.model, self.prompt_mode, mode_reason)
        if self.prompt_mode == "finetune" and self.engine_confirmed:
            log.info(
                "[local] confirmed from the rule engine without the model: %s", ", ".join(sorted(self.engine_confirmed))
            )
        if self.prompt_mode == "finetune" and self.question_rules:
            log.info("[local] YES/NO questions for checks: %s", ", ".join(sorted(self.question_rules)))
        if not self.base_url:
            # No server configured: download HF_MODEL from Hugging Face (first run) and run it in this process.
            self._tokenizer, self._model = self._load_in_process()
            self.always_prefill = not getattr(self._tokenizer, "chat_template", None)
            if self.always_prefill and self.prompt_mode == "standard":
                log.warning(
                    "%s has no chat template (base model). Use the -Instruct version for reliable audits.", self.model
                )
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
        token = (os.getenv("HF_TOKEN") or "").strip() or None
        if "gguf" in self.model.casefold():
            raise RuntimeError(
                f"HF_MODEL {self.model} is a GGUF repo, which transformers cannot load. Use a safetensors model id "
                "(e.g. unsloth/gemma-4-12B-it-qat-q4_0-unquantized), or serve the GGUF with Ollama / LM Studio "
                "and set LLM_BASE_URL."
            )
        quant, quant_reason = _resolve_quant(self.model, token)
        key = (self.model, adapter, quant)
        if key in self._loaded:
            return self._loaded[key]
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        kwargs: dict[str, Any] = {"token": token, "device_map": "auto"}
        if quant == "none":
            kwargs["dtype"] = "auto"
        else:
            from transformers import BitsAndBytesConfig

            kwargs["quantization_config"] = (
                BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_compute_dtype=torch.bfloat16)
                if quant == "4bit"
                else BitsAndBytesConfig(load_in_8bit=True)
            )
        cached = Path(self.model).is_dir() or _is_cached_locally(self.model)
        log.info("[local] %s: quantization=%s (%s)", self.model, quant, quant_reason)
        log.info(
            "[local] %s ...",
            "found in local cache, loading from disk" if cached else "not cached yet, downloading from Hugging Face",
        )
        tokenizer = AutoTokenizer.from_pretrained(self.model, token=token)
        model = AutoModelForCausalLM.from_pretrained(self.model, **kwargs)
        if adapter:
            from peft import PeftModel

            model = PeftModel.from_pretrained(model, adapter, token=token)
        model.eval()
        self._loaded[key] = (tokenizer, model)
        return self._loaded[key]

    def _generate_raw_batch(self, texts: list[str], max_tokens: int, stop: str | None = None) -> list[str]:
        """Generate for several raw prompts in one batch on the in-process model.

        Decoding is the slow part and a batch decodes all prompts together, so a few short answers cost about
        as much as one. Prompts are left-padded so every answer starts right after its own prompt.
        """
        import torch

        tokenizer, model = self._tokenizer, self._model
        assert tokenizer is not None and model is not None, "in-process model is not loaded"
        extra = {}
        if stop:
            from transformers import StoppingCriteria, StoppingCriteriaList

            class _TailStop(StoppingCriteria):
                """Stop each sequence once its last few generated tokens contain `stop` (cheap per step,
                unlike transformers' stop_strings, which indexes the whole vocabulary on every call)."""

                def __init__(self, start: int) -> None:
                    self.start = start

                def __call__(self, input_ids, scores, **kwargs):  # noqa: ANN001
                    tails = tokenizer.batch_decode(input_ids[:, max(self.start, input_ids.shape[1] - 8) :])
                    return torch.tensor([stop in tail for tail in tails], device=input_ids.device)

            extra = {"stopping_criteria": StoppingCriteriaList([_TailStop(0)])}
        side, pad = tokenizer.padding_side, tokenizer.pad_token
        try:
            tokenizer.padding_side = "left"
            if tokenizer.pad_token is None:
                tokenizer.pad_token = tokenizer.eos_token
            inputs = tokenizer(texts, return_tensors="pt", padding=True, add_special_tokens=False).to(model.device)
            if extra:  # only look at newly generated tokens, never the prompt's own text
                extra["stopping_criteria"][0].start = inputs["input_ids"].shape[1]
            with torch.inference_mode():
                output = model.generate(
                    **inputs, max_new_tokens=max_tokens, do_sample=False, pad_token_id=tokenizer.pad_token_id, **extra
                )
        finally:
            tokenizer.padding_side, tokenizer.pad_token = side, pad
        start = inputs["input_ids"].shape[1]
        answers = [tokenizer.decode(row[start:], skip_special_tokens=True) for row in output]
        if stop:  # a stopped answer may carry padding or a few tokens past the stop string
            answers = [a[: a.index(stop) + len(stop)] if stop in a else a for a in answers]
        return answers

    def _generate(self, system: str, prompt: str, max_tokens: int, prefix: str, raw: bool = False) -> str:
        """Generate from the in-process model; raw=True sends `prompt` as-is, without any chat template."""
        import torch

        tokenizer, model = self._tokenizer, self._model
        assert tokenizer is not None and model is not None, "in-process model is not loaded"
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        if raw:
            text = prompt
        elif getattr(tokenizer, "chat_template", None):

            def render(msgs: list) -> str:
                try:
                    return tokenizer.apply_chat_template(
                        msgs, tokenize=False, add_generation_prompt=True, enable_thinking=False
                    )
                except TypeError:
                    return tokenizer.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)

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
        inputs = tokenizer(text, return_tensors="pt", add_special_tokens=False).to(model.device)
        with torch.inference_mode():
            output = model.generate(**inputs, max_new_tokens=max_tokens, do_sample=False)
        return prefix + tokenizer.decode(output[0][inputs["input_ids"].shape[1] :], skip_special_tokens=True)

    def _audit_finetune(self, prompt: str, max_tokens: int) -> dict[str, Any]:
        """Ask a fine-tuned section classifier in its training format and map its answer onto the candidates."""
        task = parse_section_task(prompt)
        section = str(task.get("section", "")).strip()
        candidates = flatten_candidates(task.get("candidates"))
        in_scope = trained_rule_ids(section)
        fields = task.get("fields_used") or {}
        text = build_finetune_prompt(section, fields)
        flagged: dict[str, Mapping[str, Any]] = {}
        budget = max(max_tokens, 512)
        line_answers: dict[str, dict[str, Mapping[str, Any]]] | None = None
        if in_scope and candidates and text:
            lines = self._line_prompts(section, fields, candidates)
            if lines and self.client is None:
                # The section answer and every line's own answer in one GPU batch: about the time of one answer,
                # instead of the line re-checks running after it (past history took 8-14 s that way).
                answers = (
                    self._answer_finetune if self.fast_concerns else lambda t, b, _s: self._generate_raw_batch(t, b)
                )([text, *lines.values()], budget, section)
                content = answers[0]
                line_answers = {}
                for line, answer in zip(lines, answers[1:], strict=False):
                    try:
                        line_answers[line] = dict(self._detected(answer))
                    except ValueError:
                        line_answers[line] = {}  # an unreadable line answer confirms nothing for that line
            else:
                content = self._complete_raw(text, budget, section)
            self.last_output_character_count = len(content)
            self.last_response_text_preview = content[:1000]
            flagged = self._detected(content)
            if self.line_answers_add and line_answers:
                flagged = dict(flagged)
                for rules in line_answers.values():
                    for rule, entry in rules.items():
                        flagged.setdefault(rule, entry)
        excluded =self._unflagged_lines(section, fields, candidates, flagged, budget, line_answers)
        findings = findings_from_detected(
            [c for c in candidates if str(c.get("candidate_id", "")) not in excluded],
            flagged,
            in_scope,
            self.unsupported_policy,
            engine_confirmed=self.engine_confirmed,
        )
        if line_answers:  # a line confirmed by its own answer gets that answer's explanation, not the section's
            label = _LINE_LABELS.get(section, "")
            for f in findings:
                line = str(f.get("evidence", "")).split(" | ", 1)[0].removeprefix(label).strip()
                own = str(
                    (line_answers.get(line) or {}).get(_trained_rule(f.get("rule_id", "")), {}).get("why", "") or ""
                ).strip()
                if own:
                    f["why"] = own
        if self.model_first:
            findings += self._model_first_findings(section, fields, candidates, flagged, line_answers)
        return {"section": section, "findings": findings}

    def _model_first_findings(
        self,
        section: str,
        fields: Mapping[str, Any],
        candidates: list[Mapping[str, Any]],
        flagged: Mapping[str, Mapping[str, Any]],
        line_answers: Mapping[str, Mapping[str, Mapping[str, Any]]] | None,
    ) -> list[dict[str, Any]]:
        """Findings for rules the model flagged although no rule-engine check raised them.

        The rule engine only proposes what its hand-written checks look for; anything else the model found was
        dropped. Here the model decides on its own: a flagged rule with no matching candidate becomes a finding.
        For complaint and history lines only lines the model flags when asked about that line alone count, so a
        problem seen elsewhere in the prescription is not pinned on them.
        """
        covered = {
            t
            for c in candidates
            for t in RULE_ALIASES.get(str(c.get("rule_id", "")).strip(), (str(c.get("rule_id", "")).strip(),))
        }
        out = []
        field = _LINE_FIELDS.get(section)
        for rule, entry in flagged.items():
            if rule in covered or rule in self.engine_confirmed or rule in self.model_first_exclude:
                continue
            why, correction = RULE_TEXT.get(rule, (str(entry.get("concern", "") or ""), ""))
            if field is not None:
                for line, rules in (line_answers or {}).items():
                    if rule in rules:
                        own = str(rules[rule].get("why", "") or "").strip()
                        out.append(
                            _model_finding(
                                section, rule, entry, own or why, correction, f"{_LINE_LABELS[section]}{line}"
                            )
                        )
                continue
            evidence = str(fields.get(_SECTION_FIELD.get(section, ""), "") or "")
            out.append(
                _model_finding(
                    section, rule, entry, str(entry.get("why", "") or "").strip() or why, correction, evidence
                )
            )
        return out

    def _complete_raw(self, text: str, budget: int, section: str = "") -> str:
        if self.client is None:
            if self.fast_concerns:
                return self._answer_finetune([text], budget, section)[0]
            return self._generate("", text, budget, "", raw=True)
        response = self.client.completions.create(model=self.model, prompt=text, temperature=0, max_tokens=budget)
        return response.choices[0].text or ""

    def _answer_finetune(self, texts: list[str], budget: int, section: str) -> list[str]:
        """Generate fine-tune answers, writing each rule's known concern text instead of letting the model spell it.

        The trained answer copies the full rule description after every rule it reports
        ({"rule_id": ..., "rule_name": ..., "concern": "<up to ~150 tokens>"}). The pipeline only reads the rule
        ids, and decoding that text one token at a time was most of the wait (a section with findings took
        10-20 s, a clean one under 2 s). Generation stops where a concern starts; the exact text from the
        taxonomy is appended (prefill is fast) and the model continues with its next decision. The rules
        chosen are the model's own: the inserted text is the one it was trained to write there.
        """
        started, fallbacks_before, rounds = time.perf_counter(), self.fast_fallbacks, 0
        entry = load_taxonomy().get(section) or {}
        by_name = {(t["rule_id"], t["rule_name"]): t["concern"] for t in entry.get("allowed_error_types", [])}
        by_id = {t["rule_id"]: t["concern"] for t in entry.get("allowed_error_types", [])}
        written = [""] * len(texts)
        expect = [""] * len(texts)  # the rest of the inserted concern the model must write next
        failed: set[int] = set()
        active = list(range(len(texts)))
        for _ in range(16):  # one round per reported rule, plus the closing round
            if not active:
                break
            # A healthy round writes the rest of one word, the closing, and the next rule's id and name (or the end
            # of the answer): far fewer tokens than this cap. A longer round has gone off its trained text.
            outs = self._generate_raw_batch(
                [texts[i] + written[i] for i in active], _FAST_ROUND_TOKENS, stop=_CONCERN_OPEN
            )
            rounds += 1
            still = []
            for i, out in zip(active, outs, strict=False):
                if expect[i] and not out.startswith(expect[i]):
                    failed.add(i)  # did not finish the inserted description as trained
                    continue
                written[i] += out
                expect[i] = ""
                if not out.endswith(_CONCERN_OPEN):
                    continue  # finished, or hit the cap (checked below)
                ids = re.findall(r'"rule_id":\s*"([^"]*)"', written[i])
                names = re.findall(r'"rule_name":\s*"([^"]*)"', written[i])
                concern = by_name.get((ids[-1] if ids else "", names[-1] if names else "")) or by_id.get(
                    ids[-1] if ids else ""
                )
                if concern is None:
                    failed.add(i)  # a rule the taxonomy does not know: decode this answer normally
                    continue
                # Insert the known description up to the space before its last word; the model writes that word
                # and the closing. Tokens carry their leading space, and the end of a description and the closing
                # brackets are often one token: cutting elsewhere put the model off its trained text.
                known = json.dumps(concern, ensure_ascii=False)[1:-1]
                cut = known.rfind(" ")
                cut = cut if cut > 0 else 0
                written[i] += known[:cut]
                expect[i] = known[cut:]
                still.append(i)
            active = still
        for i, text in enumerate(written):
            if i not in failed:
                try:
                    parse_json_value(text)
                    continue
                except ValueError:
                    pass
            # Any doubt: decode this answer the normal way, so the fast path is never less correct.
            fb_started = time.perf_counter()
            written[i] = self._generate("", texts[i], budget, "", raw=True)
            self.fast_fallbacks += 1
            log.info(
                "[finetune] section=%s prompt %d: fallback to normal decoding took %.1fs (%d chars)",
                section,
                i,
                time.perf_counter() - fb_started,
                len(written[i]),
            )
        # Timing line per check (audit_server.log), to trace slow checks: rounds of fast decoding, fallbacks.
        log.info(
            "[finetune] section=%s prompts=%d rounds=%d fallbacks=%d took=%.1fs answer_chars=%s",
            section,
            len(texts),
            rounds,
            self.fast_fallbacks - fallbacks_before,
            time.perf_counter() - started,
            [len(w) for w in written],
        )
        return written

    @staticmethod
    def _detected(content: str) -> dict[str, Mapping[str, Any]]:
        try:
            payload = parse_json_value(content)
        except ValueError:
            payload = repair_json_object(content)
            if payload is None:
                raise
        return detected_rule_map(payload)

    def _line_prompts(
        self, section: str, fields: Mapping[str, Any], candidates: list[Mapping[str, Any]]
    ) -> dict[str, str]:
        """One training-format prompt per distinct line named by a line candidate this model checks.

        Only age, sex and the one line: the synthetic training rows for these line checks look like this, and
        other sections' text (a wrong complaint seen while checking past history) cannot leak in.
        """
        field = _LINE_FIELDS.get(section)
        if field is None:
            return {}
        in_scope = trained_rule_ids(section)
        base = {key: fields.get(key, "") for key in ("Age", "Gender")}
        prompts: dict[str, str] = {}
        for c in candidates:
            rule = str(c.get("rule_id", "")).strip()
            trained = [t for t in RULE_ALIASES.get(rule, (rule,)) if t in in_scope and t not in self.engine_confirmed]
            line = _candidate_line(c)
            if trained and line and line not in prompts:
                prompts[line] = build_finetune_prompt(section, {**base, field: line}) or ""
        return {line: text for line, text in prompts.items() if text}

    def _unflagged_lines(
        self,
        section: str,
        fields: Mapping[str, Any],
        candidates: list[Mapping[str, Any]],
        flagged: Mapping[str, Mapping[str, Any]],
        budget: int,
        line_answers: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None,
    ) -> set[str]:
        """Candidate ids to drop because the model, asked about their line alone, did not flag the rule.

        The model answers once per section ("a complaint does not fit this patient's sex"), but the rule engine
        raises some checks on every complaint or history line. Without this, every line was confirmed and the
        alert pointed at the first line, not the one at fault. Each line is asked in the same training format
        with only age, sex and that line; lines the model does not flag alone are dropped.
        """
        field = _LINE_FIELDS.get(section)
        if field is None or not flagged:
            return set()
        excluded: set[str] = set()
        for trained_id in flagged:
            if trained_id in self.engine_confirmed:
                continue
            group = [
                (c, line)
                for c in candidates
                if trained_id
                in RULE_ALIASES.get(str(c.get("rule_id", "")).strip(), (str(c.get("rule_id", "")).strip(),))
                and (line := _candidate_line(c))
            ]
            if not group:
                continue
            if line_answers is not None and all(line in line_answers for _, line in group):
                keep = [c for c, line in group if trained_id in line_answers[line]]
            else:
                prompts = self._line_prompts(section, fields, [c for c, _ in group])
                texts = [prompts[line] for _, line in group]
                if self.client is None:
                    answers = (
                        self._answer_finetune(texts, budget, section)
                        if self.fast_concerns
                        else self._generate_raw_batch(texts, budget)
                    )
                else:
                    answers = [self._complete_raw(text, budget, section) for text in texts]
                keep = []
                for (candidate, _), answer in zip(group, answers, strict=False):
                    try:
                        if trained_id in self._detected(answer):
                            keep.append(candidate)
                    except ValueError:
                        continue  # an unreadable per-line answer confirms nothing for that line
            # A problem no single line shows on its own is not pinned to every line (it came from elsewhere in
            # the prescription, such as the complaint, and is reported there).
            excluded |= {str(c.get("candidate_id", "")) for c, _ in group if c not in keep}
        return excluded

    def _audit_by_questions(self, prompt: str) -> dict[str, Any]:
        """Ask the model one YES/NO question per candidate check; each YES becomes a finding."""
        task = parse_section_task(prompt)
        fields = task.get("fields_used") or {}
        findings, answers = [], []
        for candidate in flatten_candidates(task.get("candidates")):
            if str(candidate.get("rule_id", "")).strip() not in self.question_rules:
                continue
            question = build_knowledge_question(candidate, fields)
            if question is None:
                continue
            if self.client is None:
                answer = self._generate(_KNOWLEDGE_SYSTEM, question, 60, "")
            else:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "system", "content": _KNOWLEDGE_SYSTEM}, {"role": "user", "content": question}],
                    temperature=0,
                    max_tokens=60,
                )
                answer = response.choices[0].message.content or ""
            answers.append(f"{candidate.get('rule_id')}: {answer.strip()}")
            finding = finding_from_answer(candidate, answer, question)
            if finding is not None:
                findings.append(finding)
        text = "\n".join(answers)
        self.last_output_character_count = len(text)
        self.last_response_text_preview = text[:1000]
        return {"section": task.get("section", ""), "findings": findings}

    # One model call at a time on the GPU. The clinical reviewer switches the fine-tune adapter off for its call;
    # without this lock a layer-1 check running on another server thread could decode without its adapter.
    _gpu_lock = threading.RLock()

    def can_review(self) -> bool:
        """The reviewer needs the base model: loaded as base + HF_ADAPTER (not a merged fine-tune)."""
        return self.client is None and hasattr(self._model, "disable_adapter")

    def review(
        self, messages: list[dict[str, str]], max_tokens: int = 300, prefix: str = '{"problems": [', stop: str = "]}"
    ) -> str:
        """Second-layer clinical review (audit_pipeline/clinical_reviewer.py): the same weights with the fine-tune
        adapter switched off, in the base model's chat format.

        The answer is started with `prefix` and stops when the JSON closes: without it the model sometimes reasoned
        in prose until the token limit (21 s, and the cut-off answer was unreadable, so its findings were lost)."""
        import torch

        if not self.can_review():
            raise RuntimeError("clinical reviewer needs HF_MODEL = base model and HF_ADAPTER = the fine-tune adapter")
        tokenizer, model = self._tokenizer, self._model
        try:
            text = tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True, enable_thinking=False
            )
        except TypeError:
            text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        text += prefix
        inputs = tokenizer(text, return_tensors="pt", add_special_tokens=False).to(model.device)
        with self._gpu_lock, torch.inference_mode(), model.disable_adapter():
            output = model.generate(
                **inputs, max_new_tokens=max_tokens, do_sample=False, stop_strings=[stop], tokenizer=tokenizer
            )
        return prefix + tokenizer.decode(output[0][inputs["input_ids"].shape[1] :], skip_special_tokens=True)

    def audit(self, *, system: str, prompt: str, max_tokens: int) -> Any:
        with self._gpu_lock:
            return self._audit_locked(system=system, prompt=prompt, max_tokens=max_tokens)

    def _audit_locked(self, *, system: str, prompt: str, max_tokens: int) -> Any:
        self.last_output_character_count = 0
        self.last_response_text_preview = ""
        section = re.search(r'REQUIRED_JSON_SKELETON=\{"section":"([A-Za-z]+)"', prompt)
        if self.prompt_mode == "finetune":
            result = self._audit_finetune(prompt, max_tokens)
            if self.question_rules:
                found = {item["candidate_id"] for item in result["findings"]}
                asked = self._audit_by_questions(prompt)["findings"]
                result["findings"] += [item for item in asked if item["candidate_id"] not in found]
            return result
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
    selected = (name or os.getenv("AUDIT_PROVIDER") or os.getenv("LLM_PROVIDER") or "azure").strip().casefold()
    if selected == "azure":
        return AzureOpenAIProvider()
    if selected == "groq":
        return GroqProvider()
    if selected == "gemma":  # fine-tuned section classifier served by serve_gemma4.py
        from .gemma_provider import LocalGemmaProvider

        return LocalGemmaProvider()
    if selected in {"local", "hf"}:  # any HF chat model run in-process, or Ollama / LM Studio / vLLM via LLM_BASE_URL
        return LocalChatProvider()
    raise RuntimeError(f"Unsupported audit provider {selected!r}; expected azure, groq, gemma or local")


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
