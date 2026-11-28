# Serving PrescribeX with vLLM (plan tasks T01.1–T01.3)

**State 21 Nov 2026:** installed and ready to start. WSL2 Ubuntu 24.04 (user `prescribex`), venv `~/vllm-env` with
**vLLM 0.34.0** (torch 2.14.0, CUDA 13.0, transformers 5.22.2), model and adapter copied to `~/models` (adapter
sha256 prefix 8d9d825dc44bfa4b checked). vLLM 0.34.0 registers `Gemma4UnifiedForConditionalGeneration` natively and
reports **LoRA support = True** for it.

**Result 23 Nov 2026 — running and tested (bf16, GPU_UTIL 0.95, KV cache 14,621 tokens):**
- LoRA is applied: the same CC prompt gives the trained answer (CC.3 duration missing) on `prescribex` and
  unrelated text on the base model.
- Equivalence (`docs/vllm_equivalence_report.md`, 99 section checks vs the current 4-bit engine): layer 1 **100%**
  same alerts, layer 2 **96%** (target 95%). Median per check: layer 1 2.05 s → 0.59 s, layer 2 2.72 s → 0.63 s.
- Load (`docs/vllm_load_report.md`): 1 / 4 / 8 / 16 doctors at once = 25 / 63 / 98 / 116 section checks per
  minute, layer-1 median 0.7 / 1.0 / 1.3 / 2.8 s, 0 errors.
- Start-up problems found and fixed: (1) vLLM 0.34 crashes reading `vision_config.num_soft_tokens` → start with
  `--language-model-only` (text only); (2) torch compile needs gcc → `build-essential` in the install script;
  (3) at GPU_UTIL 0.88 the KV cache did not fit one 8K request → 0.95.
- More doctors: the KV cache limits concurrency. `QUANT=fp8` (FP8 weights, ~12 GB) frees ~10 GB more KV cache;
  re-run the equivalence check before using it in the pilot.

Quick start (two PowerShell windows):

```powershell
.\deploy\vllm\start_vllm.ps1
```

```powershell
.\run_local.ps1
```

(`.env`: `AUDIT_PROVIDER=vllm`. Setup from scratch: `wsl --install -d Ubuntu-24.04`, then
`bash deploy/vllm/install_vllm.sh` inside Ubuntu.)

## 1. Can vLLM run our model? (T01.1 check)

Our model: base `unsloth/gemma-4-12B-it-qat-q4_0-unquantized` (architecture `Gemma4UnifiedForConditionalGeneration`,
bf16, 23.9 GB) + LoRA `mapping_v5/adapter_gold` (r 16, alpha 32; q/k/v/o and gate/up/down projections in the 48
text layers only, no vision or audio parts; v_proj in 40 layers because the 8 full-attention layers share k and v).

| Question | Answer | Source |
|---|---|---|
| Does vLLM support this architecture? | Yes, natively, in releases after v0.22.1 (PR #44429, "Gemma4 Unified (encoder-free)"). v0.22.x fell back to a slower generic path and failed on the QAT checkpoint. | vLLM issue #44796; `gemma4_unified` docs page |
| LoRA on it? | The model docs say LoRA is inherited from `Gemma4ForConditionalGeneration` (LoRA ✅). The supported-models table leaves the LoRA cell empty for the unified class. **Not certain until we try it.** | vLLM docs |
| Known LoRA bug? | On v0.22.0 a Gemma 4 LoRA was silently **not applied** (answers identical to the base model). Fixed by PR #39816; on a later build LoRA answers matched transformers on 99.93% of 29,600 rows. | vLLM issue #47026 |
| Attention kernels | In June the FlashAttention/FlashInfer backends rejected the 512-wide full-attention heads; the workaround was `TRITON_ATTN`. May be fixed now; the start script has `ATTN=TRITON_ATTN` as a switch. | vLLM issues #44796, #40677 |
| Windows? | Not supported natively; WSL2 is the supported route. | vLLM install docs |

Conclusion: worth trying with the **newest stable vLLM**. The risk is the LoRA, so the first test checks that the
LoRA changes the answers (step 6) before any comparison.

## 2. This PC

| Item | Found |
|---|---|
| GPU | RTX 5090 32 GB, driver 610.88, limit 400 W, clock 2092 MHz (21 Nov) |
| WSL | WSL 2.7.12, kernel 6.18 — **no Linux distribution installed yet** |
| RAM / disk | 128 GB (WSL gets half by default = 64 GB, enough) / 1.5 TB free on C: |
| Model files | base model already in the Windows Hugging Face cache; adapter in `LLM_Trainer/outputs/...` — **no download of the model needed** |

GPU memory: bf16 weights ~24 GB of 32 GB leaves ~4–5 GB for the KV cache at `GPU_UTIL=0.88`. Enough for the
equivalence test; for many doctors at once (T02) use FP8 weights (T02.2).

## 3. Install steps (need Tanim's OK first: downloads ~0.5 GB Ubuntu + ~8–10 GB Python packages)

Run in PowerShell:

```powershell
wsl --install -d Ubuntu-24.04
```

Then in the Ubuntu window (make a user when asked):

```bash
nvidia-smi                                   # must show the RTX 5090 (the Windows driver is shared, no CUDA install)
sudo apt update && sudo apt install -y python3-dev build-essential
curl -LsSf https://astral.sh/uv/install.sh | sh && source ~/.bashrc
uv venv ~/vllm-env --python 3.12 && source ~/vllm-env/bin/activate
uv pip install vllm                          # newest stable; write the version into the model register after the test
python -c "import vllm, torch; print(vllm.__version__, torch.cuda.get_device_name(0))"
```

Step 4: copy the model and adapter to the Linux disk (24 GB, a few minutes; reading through `/mnt/c` at every start
is much slower):

```bash
mkdir -p ~/models
cp -rL /mnt/c/Users/HEAT/.cache/huggingface/hub/models--unsloth--gemma-4-12B-it-qat-q4_0-unquantized/snapshots/a9e15ead91dc20268a5d25117ce806a0049d27b7 ~/models/gemma-4-12B-it-qat-q4_0-unquantized
cp -r "/mnt/c/Users/HEAT/Documents/LLM_Trainer/outputs/unsloth__gemma-4-12B-it-qat-q4_0-unquantized__mapping_v5/adapter_gold" ~/models/prescribex_mapping_v5_adapter_gold
sha256sum ~/models/prescribex_mapping_v5_adapter_gold/adapter_model.safetensors | cut -c1-16   # must be 8d9d825dc44bfa4b
```

Step 5: start (first check the power cap in Windows: `nvidia-smi --query-gpu=power.draw,power.limit,clocks.gr --format=csv`):

```bash
cd "/mnt/c/Users/HEAT/Documents/Local Pipeline/Local_pip" && bash deploy/vllm/start_vllm.sh
```

Windows reaches it at `http://127.0.0.1:8000/v1` (WSL forwards localhost).

## 4. First checks (step 6)

1. `curl http://127.0.0.1:8000/v1/models` lists both `unsloth/gemma-4-12B-it-qat-q4_0-unquantized` and `prescribex`.
2. **LoRA is really on:** send one training-format prompt to both names. `prescribex` must answer in the trained
   JSON (`{"has_error": ..., "detected_errors": [...]}`); the base name answers differently. Identical answers = the
   LoRA bug of issue #47026: stop and try a newer vLLM or the fallback.
3. If loading fails on the adapter's regex `target_modules`: make a copy of the adapter folder whose
   `adapter_config.json` lists `["q_proj","k_proj","v_proj","o_proj","gate_proj","up_proj","down_proj"]` (same
   weights file) and point `ADAPTER_DIR` at it. Do not change the original.

## 5. Connect the app (T01.2, code done)

In `.env` (keep a backup first): `AUDIT_PROVIDER=vllm`, keep `HF_MODEL` = the base model id, optionally
`VLLM_BASE_URL`, `VLLM_LORA_NAME`, `AUDIT_CONCURRENCY` (parallel section checks the audit server lets through;
default 2, raise it for the load test). `HF_ADAPTER` and `HF_QUANT` are not used with vllm. Then `.\run_local.ps1`.
Switching back = `AUDIT_PROVIDER=local`. The local provider stays the default.

How it works (`audit_pipeline/vllm_provider.py`): the same audit code as the local provider; only the model calls go
to vLLM. Layer-1 requests name `prescribex` (LoRA on), layer-2 requests name the base model (LoRA off). The prompts
are built and tokenized in the app with the model's own tokenizer and sent as token ids, so vLLM sees the same tokens
as the in-process model. There is no GPU lock any more: both layers and several doctors can be served at once.
Offline check: `python -m benchmark.vllm_provider_selftest` (a stand-in server; 9 of 9 checks pass).

## 6. Equivalence check (T01.3)

`benchmark/vllm_equivalence.py` sends the 36 team-written test cases (doctor 15, suite 13, correct 8; no patient
data) section by section to both layers: 99 section checks per layer, more than the 40 in the plan.

```powershell
# current engine (AUDIT_PROVIDER=local), .\run_local.ps1 running
.venv\Scripts\python.exe -m benchmark.vllm_equivalence record --out benchmark/results/equiv_local.json
# stop it, start vLLM, AUDIT_PROVIDER=vllm, .\run_local.ps1 again
.venv\Scripts\python.exe -m benchmark.vllm_equivalence record --out benchmark/results/equiv_vllm.json --layer1-from benchmark/results/equiv_local.json
.venv\Scripts\python.exe -m benchmark.vllm_equivalence compare benchmark/results/equiv_local.json benchmark/results/equiv_vllm.json
```

It writes `docs/vllm_equivalence_report.md`: share of section checks with the same alert list per layer (target
95% or more), the median time of each engine, and every difference. If agreement is under 95%: check `HF_QUANT` of
the local run (the register's v5 settings use 4-bit, vLLM runs bf16 — compare against a 16-bit local run,
`HF_QUANT=none`), then decoding settings (temperature 0, max tokens, stop strings).

## 7. Fallback: SGLang

If vLLM cannot load the model with the LoRA, or the LoRA is not applied: SGLang supports Gemma 4 (including the 12B
unified variant, per its Gemma 4 cookbook) and multi-LoRA serving (`--enable-lora --lora-paths prescribex=<folder>
--max-lora-rank 16`); Gemma-4-specific LoRA support is not confirmed in its docs. Install in the same WSL venv
setup (`uv pip install "sglang[all]"`). The provider would need one small change: SGLang picks the adapter with a
`lora_path` field (or `model: "<base>:<lora>"`) instead of the model name, and keeps the stop string with
`no_stop_trim`. If both fail: keep the current engine and add request batching (plan T01.1 fallback).

Sources: [vLLM #44796](https://github.com/vllm-project/vllm/issues/44796),
[vLLM #47026](https://github.com/vllm-project/vllm/issues/47026),
[vLLM #40693](https://github.com/vllm-project/vllm/issues/40693),
[gemma4_unified docs](https://docs.vllm.ai/en/latest/api/vllm/model_executor/models/gemma4_unified/),
[vLLM supported models](https://docs.vllm.ai/en/latest/models/supported_models/),
[vLLM GPU install](https://docs.vllm.ai/en/latest/getting_started/installation/gpu/),
[SGLang LoRA serving](https://sgl-project.github.io/advanced_features/lora.html),
[SGLang Gemma 4 cookbook](https://docs.sglang.io/cookbook/autoregressive/Google/Gemma4).
