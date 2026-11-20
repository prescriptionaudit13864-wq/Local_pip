# PrescribeX: Live Prescription Audit

A prescription-writing app for Bangladesh primary care with a live clinical audit. As the doctor completes each
section (complaints, history, investigations, diagnosis, medicines, advice), the section is checked in the context of
the prescription so far, and the doctor sees specific alerts with a short reason. A final audit runs when the
prescription is saved.

Everything runs locally on one GPU: no patient data leaves the machine.

Full technical documentation (data, knowledge base, pipeline, model, training, evaluation):
[`docs/PrescribeX_Technical_Documentation.md`](docs/PrescribeX_Technical_Documentation.md).

## How it works

```
Doctor saves a section
  ├─► Layer 1  fast check       Gemma 4 12B + fine-tuned LoRA adapter   42 trained rules, drug facts   ~1.8 s
  └─► Layer 2  clinical review  same weights, adapter switched off      entry by entry, self-check     ~2.2 s
Final save ─► full audit report
```

- Rule engines only **propose** what to check; the model decides every alert (`AUDIT_MODEL_ONLY=1`).
- Both layers share one 16-bit copy of the model (~24 GB GPU memory).

## Project layout

| Folder / file | Contents |
|---|---|
| `prescribex-app/` | React (Vite) app; `backend/` saves patients and visits locally |
| `audit_pipeline/` | Audit pipeline: patient context, medicine mapping, candidate engines, model providers, clinical reviewer, and the `app_server` HTTP bridge |
| `audit_pipeline/data/` | Medicine and investigation catalogues, ICD-11 terminology, drug and guideline fact stores |
| `audit_pipeline/configs/` | Rulebook (`protocol.yaml`), layer-1 rule list (`finetune_taxonomy.json`), medicine aliases |
| `benchmark/` | Live test suites and the model comparison benchmark |
| `docs/` | Technical documentation |
| `run_local.ps1` | Starts the audit server and the app |
| `install_model.ps1` | Installs a trained adapter and its rule list (`-Undo` restores the previous one) |

## Setup

Requirements: Windows, Python 3.11, an NVIDIA GPU with 24 GB+ (or ~12 GB with `HF_QUANT=4bit`), Node.js.

```powershell
python -m venv .venv
.venv\Scripts\pip install torch==2.11.0 --index-url https://download.pytorch.org/whl/cu128
.venv\Scripts\pip install -r requirements.txt
cd prescribex-app; npm install; cd ..
```

Create `.env` in the project root (never committed):

```
AUDIT_PROVIDER=local
HF_MODEL=unsloth/gemma-4-12B-it-qat-q4_0-unquantized
HF_ADAPTER=<path to the trained adapter_gold folder>
AUDIT_PROMPT_MODE=finetune
HF_QUANT=none                  # none | 4bit | 8bit
FINETUNE_UNSUPPORTED=omit
AUDIT_MODEL_ONLY=1
AUDIT_REVIEWER=1               # 0 turns the clinical reviewer (layer 2) off
```

The base model downloads from Hugging Face on first start. Every setting is described in the technical
documentation (section 10).

## Running

```powershell
.\run_local.ps1
```

`run_local.ps1` reads `.env` fresh, starts the audit server on port 8765, waits until the model is loaded, then opens
the app. Server logs go to `audit_server.log` / `audit_server.err.log`.

The audit server can also be run on its own:

```powershell
.venv\Scripts\python -m audit_pipeline.app_server serve                 # HTTP API on http://127.0.0.1:8765
.venv\Scripts\python -m audit_pipeline.app_server audit path\to\visit.json
```

To use the app from another computer, start the server with `--host 0.0.0.0` and run the app there with
`AUDIT_BRIDGE_URL=http://<this-pc>:8765 npm run dev`. The server has no login: use it only on a trusted network.

## Testing

With the app running:

```powershell
.venv\Scripts\python -m benchmark.live_suite          # 13 end-to-end cases, all sections
.venv\Scripts\python -m benchmark.doctor_probe        # 15 cases reported by the project doctor
.venv\Scripts\python -m benchmark.live_review_probe   # both layers: doctor + suite + 8 correct prescriptions
```

## Model benchmark

`benchmark/` also replays prescriptions through the pipeline section by section with different local models, and
scores each model's findings against known planted errors.

| File | Purpose |
|---|---|
| `run_benchmark.py` | Runs every model in `models.json` (`run: true`) on the test prescriptions |
| `models.json` | The models compared, plus a `rules-only` baseline with no LLM |
| `test_prescriptions.json` | 10 synthetic prescriptions with 11 planted errors (8 critical, 3 major) |
| `synthetic_prescriptions_all.json` | Full set of 35 synthetic cases (29 with errors, 6 clean) |
| `export_doctor_review.py` | Exports results as CSV/Excel with columns for a doctor to verify each finding |
| `results/2026-10-30_model_comparison/` | Results for 16 models + baseline (predates the fine-tuned model) |

```powershell
.venv\Scripts\python benchmark\run_benchmark.py                          # all models, 10 prescriptions
.venv\Scripts\python benchmark\run_benchmark.py --models unsloth/Qwen3-8B-bnb-4bit --limit 2
.venv\Scripts\python benchmark\export_doctor_review.py                   # CSV/Excel for the latest run
```

The synthetic prescriptions use fictional patients and are for evaluation only, not medical advice.

### Real prescriptions

1. Copy `benchmark/real_prescriptions/annotation_template.xlsx` into `benchmark/real_prescriptions/data/` and fill it
   in: prescriptions, medicines, and one row per error the doctor finds.
2. Convert and run:

```powershell
.venv\Scripts\python benchmark\real_prescriptions.py convert
.venv\Scripts\python benchmark\run_benchmark.py --model-list benchmark\models_top5.json --cases benchmark\real_prescriptions\data\real_prescriptions.json --out benchmark\results_real\top5
.venv\Scripts\python benchmark\export_doctor_review.py --run benchmark\results_real\top5 --cases benchmark\real_prescriptions\data\real_prescriptions.json
```

`benchmark/real_prescriptions/data/` and `benchmark/results_real/` hold patient data and are excluded from git.

## Data and privacy

- Patient records (`prescribex-app/backend/data/`, the patient workbook) and real-prescription results are excluded
  from git.
- `.env` and its backups hold API keys and are excluded from git.
- The drug facts come from medex.com.bd; the guideline facts from Bangladesh national guidelines and textbooks.
