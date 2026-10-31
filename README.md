# PrescribeX (local Gemma 4 audit)

Run `.\run_local.ps1` in Windows PowerShell. It starts the Gemma server (port 8766), the audit bridge (port 8765) and the app.

Layout:
- `prescription-tool/` - React app
- `v3/` - audit pipeline and `app_server.py` bridge
- `prescription_audit_gemma4/prescription_audit_gemma4/` - `serve_gemma4.py` plus the gold LoRA adapter in `outputs/`

`.env` has `AUDIT_PROVIDER=gemma`. Set it to `groq` (with `GROQ_API_KEY`) to use the cloud model instead.

- The Gemma model is a section classifier: for CC/Dx/Inv/Rx/Adv it names the error rules it was trained on. Rule-engine candidates are confirmed only when the model flags the same rule.
- Candidates for rules the model was not trained on follow `GEMMA_UNSUPPORTED` in `.env`: `confirm_high` (default), `omit`, or `confirm`.
- The RAG router and advice-action classifier need a general chat model, so with Gemma they use their deterministic fallback.

## Excel database

`prescription-tool/backend/PrescribeX_Database.xlsx` is created automatically with three sheets:
- **Patient Info**: one row per patient (ID, name, gender, age, address, contact, blood group). Written once, never edited.
- **Prescription Data**: one row per visit. Visit ID = Patient ID + `-` + visit number (e.g. `555666-1`). It holds the screening (never edited) and, after the doctor presses Save, the final prescription.
- **Prescription Log**: Patient ID, Category, Input, LLM found errors, Timestamp. A row is added each time a section is saved, so every change the doctor makes is kept.

An older single-sheet workbook is split into these sheets automatically on the next save.
If the workbook is open in Excel, writes are kept in `backend/db_pending.json` and added on the next save. Close the file to let them through.

## Trying other models (one-line change)

Edit `.env` and run `.\run_local.ps1`:

```
AUDIT_PROVIDER=local
HF_MODEL=Qwen/Qwen2.5-7B-Instruct    # any Hugging Face model id or local folder (downloaded on first start)
HF_QUANT=none                        # none | 4bit | 8bit to fit a large model on the GPU
HF_ADAPTER=                          # optional LoRA adapter on top of HF_MODEL
HF_TOKEN=                            # only for gated models
```

`AUDIT_PROVIDER=local` downloads `HF_MODEL` from Hugging Face on first use, runs it inside the audit server, and asks it to
confirm or reject each candidate. `groq` uses the cloud model.
Ollama / LM Studio also work: set `LLM_BASE_URL=http://127.0.0.1:11434/v1` and `HF_MODEL=<their model name>`.

**Disk space:** when a start downloads a new model, the models this pipeline downloaded earlier are deleted once the new one has loaded (tracked in `prescription_audit_gemma4/prescription_audit_gemma4/downloaded_models.json`). Models that were already on your disk before the pipeline used them, and the base model of your fine-tuned adapter, are never deleted. Set `HF_KEEP_OLD_MODELS=1` in `.env` to keep everything.
