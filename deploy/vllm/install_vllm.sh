#!/usr/bin/env bash
# One-time setup inside Ubuntu (WSL2): uv, a Python 3.12 venv with vLLM, and the model + adapter on the Linux disk.
# See docs/vllm_serving_plan.md. Safe to run again: finished steps are skipped.
#
#   bash deploy/vllm/install_vllm.sh
set -euo pipefail

VENV="${VENV:-$HOME/vllm-env}"
VLLM_SPEC="${VLLM_SPEC:-vllm}"   # e.g. VLLM_SPEC=vllm==0.23.1 to pin a version
HF_SNAPSHOT="${HF_SNAPSHOT:-/mnt/c/Users/HEAT/.cache/huggingface/hub/models--unsloth--gemma-4-12B-it-qat-q4_0-unquantized/snapshots/a9e15ead91dc20268a5d25117ce806a0049d27b7}"
ADAPTER_SRC="${ADAPTER_SRC:-/mnt/c/Users/HEAT/Documents/LLM_Trainer/outputs/unsloth__gemma-4-12B-it-qat-q4_0-unquantized__mapping_v5/adapter_gold}"
MODEL_DIR="${MODEL_DIR:-$HOME/models/gemma-4-12B-it-qat-q4_0-unquantized}"
ADAPTER_DIR="${ADAPTER_DIR:-$HOME/models/prescribex_mapping_v5_adapter_gold}"

# vLLM compiles GPU kernels at start (torch inductor / triton): it needs a C compiler and Python headers.
if ! command -v gcc >/dev/null; then
  sudo apt-get update && sudo apt-get install -y build-essential python3-dev
fi
if ! command -v uv >/dev/null && [[ ! -x "$HOME/.local/bin/uv" ]]; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"

[[ -d "$VENV" ]] || uv venv "$VENV" --python 3.12
# shellcheck disable=SC1091
source "$VENV/bin/activate"
python -c "import vllm" 2>/dev/null || uv pip install "$VLLM_SPEC"
python - <<'EOF'
import torch, transformers, vllm
print("vllm", vllm.__version__, "| torch", torch.__version__, "cuda", torch.version.cuda,
      "| transformers", transformers.__version__, "|", torch.cuda.get_device_name(0))
EOF

mkdir -p "$(dirname "$MODEL_DIR")"
[[ -f "$MODEL_DIR/config.json" ]] || cp -rL "$HF_SNAPSHOT" "$MODEL_DIR"
[[ -f "$ADAPTER_DIR/adapter_model.safetensors" ]] || cp -r "$ADAPTER_SRC" "$ADAPTER_DIR"
echo "adapter sha256 prefix: $(sha256sum "$ADAPTER_DIR/adapter_model.safetensors" | cut -c1-16) (expected 8d9d825dc44bfa4b)"
du -sh "$MODEL_DIR" "$ADAPTER_DIR"
