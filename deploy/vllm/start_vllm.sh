#!/usr/bin/env bash
# Start vLLM in WSL2 for PrescribeX: Gemma 4 12B base loaded once (layer 2, clinical reviewer) and the v5
# fine-tune as a LoRA named "prescribex" (layer 1, fast check). See docs/vllm_serving_plan.md.
#
#   bash deploy/vllm/start_vllm.sh            (inside Ubuntu in WSL2, after the install steps in the plan)
#
# Check the GPU power cap on Windows first: nvidia-smi --query-gpu=power.draw,power.limit,clocks.gr --format=csv
# (limit 400 W, clock 2100 MHz or less).
set -euo pipefail

VENV="${VENV:-$HOME/vllm-env}"
# Model and adapter copied to the Linux disk (reading 24 GB through /mnt/c is slow); see the plan, step 4.
MODEL_DIR="${MODEL_DIR:-$HOME/models/gemma-4-12B-it-qat-q4_0-unquantized}"
ADAPTER_DIR="${ADAPTER_DIR:-$HOME/models/prescribex_mapping_v5_adapter_gold}"
# Must equal HF_MODEL in the app's .env: the provider sends layer-2 requests under this name.
SERVED_NAME="${SERVED_NAME:-unsloth/gemma-4-12B-it-qat-q4_0-unquantized}"
LORA_NAME="${LORA_NAME:-prescribex}"
PORT="${PORT:-8000}"
# bf16 weights take 23.6 GB of 32 GB; at 0.88 only 1.5 GB was left for the KV cache (not even one 8K request).
# 0.95 leaves ~3.5 GB KV (a few requests at once). For many doctors use QUANT=fp8: weights ~12 GB, KV ~15 GB.
GPU_UTIL="${GPU_UTIL:-0.95}"
QUANT="${QUANT:-}"               # empty = bf16 (same weights as the local engine); fp8 = online FP8 weights
MAX_LEN="${MAX_LEN:-8192}"       # longest layer-1 prompt is about 3,100 tokens; layer 2 is longer with drug facts
# Set ATTN=TRITON_ATTN if vLLM refuses the 512-wide full-attention heads with the default backend (vLLM #40677).
ATTN="${ATTN:-}"

nvidia-smi --query-gpu=power.draw,power.limit,clocks.gr,memory.used --format=csv

# shellcheck disable=SC1091
source "$VENV/bin/activate"
args=(
  "$MODEL_DIR"
  --served-model-name "$SERVED_NAME"
  --dtype bfloat16
  --max-model-len "$MAX_LEN"
  --gpu-memory-utilization "$GPU_UTIL"
  --enable-lora
  --lora-modules "$LORA_NAME=$ADAPTER_DIR"
  --max-lora-rank 16
  --max-loras 1
  # Text only: skips the vision/audio towers. Also avoids a vLLM 0.34 crash where the image budget reads
  # vision_config.num_soft_tokens, which this checkpoint's config does not have.
  --language-model-only
  --host 127.0.0.1
  --port "$PORT"
)
if [[ -n "$QUANT" ]]; then
  args+=(--quantization "$QUANT")
fi
if [[ -n "$ATTN" ]]; then
  args+=(--attention-backend "$ATTN")
fi
exec vllm serve "${args[@]}"
