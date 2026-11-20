# PrescribeX — notes for AI coding assistants

Read `docs/PrescribeX_Technical_Documentation.md` first: it describes the data, knowledge base, pipeline, the
two-layer model (fine-tuned fast check + clinical reviewer), training, evaluation and configuration.

## Rules for working in this repository
- The model decides every alert. Do not add hand-written code rules that raise alerts on their own
  (`AUDIT_MODEL_ONLY=1`, `FINETUNE_UNSUPPORTED=omit`).
- Patient data stays local. Never send prescriptions, `prescribex-app/backend/data/` or real-prescription results to
  an outside service, and never commit them (see `.gitignore`).
- `.env` and its backups hold API keys: never commit or print them.
- Never commit or push unless the user asks.

## Workflow
- Start the app with `.\run_local.ps1` (reads `.env` fresh, starts the audit server on port 8765, then the app).
- Install a trained model with `.\install_model.ps1` (undo with `-Undo`).
- Turn the clinical reviewer (layer 2) off with `AUDIT_REVIEWER=0` in `.env`.
- Live tests need the app running: `python -m benchmark.live_suite`, `benchmark.doctor_probe`,
  `benchmark.live_review_probe`.
- Port 8765 busy: `Get-NetTCPConnection -LocalPort 8765 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }`.
- The user prefers plain language and paste-ready PowerShell commands.
