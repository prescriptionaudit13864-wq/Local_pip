# PrescribeX: Prescription Audit

A prescription-writing app with a live clinical audit. As the doctor fills in each section, a rule engine raises
possible errors (drug interactions, pregnancy and allergy contraindications, dosing limits, organ impairment,
demographic conflicts) and a local language model confirms or rejects each one. A final audit runs when the
prescription is saved.

Everything runs locally: no patient data leaves the machine.

## Project layout

| Folder / file | Contents |
|---|---|
| `prescribex-app/` | React (Vite) app; `backend/` saves patients and visits to an Excel workbook |
| `audit_pipeline/` | Audit pipeline: rule and safety engines, guideline retrieval, LLM providers, and the `app_server` HTTP bridge |
| `audit_pipeline/data/` | Medicine and investigation catalogues, ICD-11 terminology, guideline and drug fact stores |
| `audit_pipeline/configs/` | Protocol rules, medicine aliases, dosing-frequency cache |
| `benchmark/` | Comparison of local 4-bit LLMs as the reviewer model (see below) |
| `run_local.ps1` | Starts the audit server and the app |

## Running the app

Requirements: Python 3.11 with a CUDA GPU, Node.js.

```powershell
python -m venv .venv
.venv\Scripts\pip install -r audit_pipeline\requirements.txt
cd prescribex-app; npm install; cd ..
.\run_local.ps1
```

`run_local.ps1` starts the audit server on port 8765 and then the app. The model is chosen in a `.env` file in the
project root (not committed):

```
AUDIT_PROVIDER=local
HF_MODEL=unsloth/Qwen3-14B-bnb-4bit   # any Hugging Face model id or local folder (downloaded on first start)
HF_QUANT=none                         # none | 4bit | 8bit (use none for repos that are already quantized)
HF_ADAPTER=                           # optional LoRA adapter on top of HF_MODEL
HF_TOKEN=                             # only for gated models
```

Ollama or LM Studio also work: set `LLM_BASE_URL=http://127.0.0.1:11434/v1` and `HF_MODEL=<their model name>`.

The audit server can also be run on its own:

```powershell
python -m audit_pipeline.app_server serve                 # HTTP API on http://127.0.0.1:8765
python -m audit_pipeline.app_server audit path\to\visit.json
```

### Patient workbook

The app writes `prescribex-app/backend/PrescribeX_Database.xlsx` with three sheets: **Patient Info** (one row per
patient), **Prescription Data** (one row per visit) and **Prescription Log** (every saved section with the errors the
audit found). The workbook and `prescribex-app/backend/data/` hold patient records and are excluded from git.

## Model benchmark

`benchmark/` replays prescriptions through the pipeline section by section, exactly as the app sends them, with
different local 4-bit models, and scores each model's findings against known planted errors.

| File | Purpose |
|---|---|
| `run_benchmark.py` | Runs every model in `models.json` (`run: true`) on the test prescriptions |
| `models.json` | The models compared, plus a `rules-only` baseline with no LLM |
| `test_prescriptions.json` | 10 synthetic prescriptions with 11 planted errors (8 critical, 3 major) |
| `synthetic_prescriptions_all.json` | Full set of 35 synthetic cases (29 with errors, 6 clean) |
| `export_doctor_review.py` | Exports results as CSV/Excel with columns for a doctor to verify each finding |
| `results/2026-10-30_model_comparison/` | Results for 16 models + baseline: one JSON per model, `summary.csv`, and `doctor_review/` |

```powershell
.venv\Scripts\pip install bitsandbytes openpyxl
.venv\Scripts\python benchmark\run_benchmark.py                          # all models, 10 prescriptions
.venv\Scripts\python benchmark\run_benchmark.py --models unsloth/Qwen3-8B-bnb-4bit --limit 2
.venv\Scripts\python benchmark\export_doctor_review.py                   # CSV/Excel for the latest run
```

Metrics per model: precision, recall, F1, critical-error recall, live-audit recall, seconds per prescription,
tokens/s, malformed-output rate, peak VRAM and load time.

The synthetic prescriptions use fictional patients and are for evaluation only, not medical advice.

## Evaluation on real prescriptions

The top 5 models from the synthetic benchmark (`benchmark/models_top5.json`: Qwen3-32B, Gemma-4-12B QAT, Phi-4,
Gemma-3-27B, Gemma-3-12B, plus the rules-only baseline) are re-tested on real prescriptions annotated by a doctor.

1. Copy `benchmark/real_prescriptions/annotation_template.xlsx` into `benchmark/real_prescriptions/data/` and fill it
   in: prescriptions, medicines (current and previous visit), and one row per error the doctor finds, with its
   section, error type, severity and the key words (drug names, terms) a correct finding should mention.
2. Run the VS Code tasks in order, or:

```powershell
.venv\Scripts\python benchmark\real_prescriptions.py convert
.venv\Scripts\python benchmark\run_benchmark.py --model-list benchmark\models_top5.json --cases benchmark\real_prescriptions\data\real_prescriptions.json --out benchmark\results_real\top5
.venv\Scripts\python benchmark\export_doctor_review.py --run benchmark\results_real\top5 --cases benchmark\real_prescriptions\data\real_prescriptions.json
```

`benchmark/real_prescriptions/data/` and `benchmark/results_real/` hold patient data and are excluded from git.
Use prescription IDs (for example `RX-001`) instead of patient names.
