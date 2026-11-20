# PrescribeX — context for Claude Code

This file is read automatically by Claude Code at the start of every session in this folder.
It summarizes a long debugging session done through claude.ai chat (with a remote bridge to this
machine), so a fresh Claude Code session has full context instead of starting blind.

## What this project is

PrescribeX: a prescription-writing app with a live clinical audit. A doctor fills in a
prescription section by section; a hybrid pipeline (deterministic rule engine + local/cloud LLM
confirmation) checks each section for errors as it's filled, then runs one final audit on save.
See `README.md` and `PROJECT_CONTEXT.md` in this folder for the full architecture — read those
first if you haven't.

## What was fixed in the last session (already committed)

All of this is in commit `645084d` ("Add fine-tune prompt mode and show possible errors in live
audit"), touching `audit_pipeline/audit_writer.py`, `audit_pipeline/provider.py`, and
`audit_pipeline/incremental_audit.py`. Run `git show 645084d --stat` to see the full diff.

### 1. `provider.py` — HF_QUANT auto-detection
`LocalChatProvider._load_in_process` used to require `HF_QUANT` to be set by hand (`none`/`4bit`/
`8bit`), and guessing wrong either failed to load or quantized an already-quantized repo again.
Added `_resolve_quant()`: when `HF_QUANT=auto` (the new default), it checks the repo's
`config.json` via `AutoConfig` and picks `none` if the repo is already quantized on the Hub
(bnb-4bit, GPTQ, AWQ, QAT...) or `4bit` otherwise. Also added `_is_cached_locally()` so startup
logs say whether it's loading from the local HF cache or downloading fresh. `.env` now documents
this (`HF_QUANT=auto`, with `none`/`4bit`/`8bit` still available as manual overrides).

### 2. `audit_writer.py` — candidate prompt bloat
`rule_candidate_engine.py` deliberately raises several narrow candidates (different rule_id, same
underlying evidence) per complaint/history line — e.g. one chief-complaint entry can produce 4-5
separate candidates (severity/site/demographic/temporal checks), each repeating the same evidence
text in the prompt. Added `_group_candidates_for_prompt()`, called from `_compact_task()`: groups
sibling candidates sharing identical `evidence` into one `{"evidence": ..., "checks": [...]}` prompt
entry, so the model reads the evidence once. **This is prompt-rendering only** — each check keeps
its own `candidate_id`/`rule_id`, and matching in `_select_candidate`/`_normalize_findings` always
runs against the original ungrouped `task["candidates"]`, never the grouped view. Added matching
instructions to `build_audit_prompt` (both the CC-specific and generic branches) explaining the
`checks` structure to the model.

### 3. `audit_writer.py` — alert-fatigue filter gaps (`_provider_rejected_candidate`)
This function is supposed to drop "confirmed" findings whose own text actually says nothing's
wrong (so the UI doesn't show empty "no issue" alerts). Found and fixed several real gaps,
each verified against synthetic test data (not just syntax-checked):
- **Bug**: "No site is documented..." (negation *before* the subject) wasn't recognized by the
  "is/are addressed/documented/..." exclusion-word list, which only checked for "not/never/
  without/..." but not bare "no". This was causing **real findings to be wrongly dropped**. Fixed
  by adding "no" to that exclusion list (it was already present in a sibling check a few lines
  below — this was an inconsistency, not an intentional design choice).
- That fix on its own then broke "no change needed" rejections (they'd coincidentally relied on
  the same broken interaction). Fixed properly by adding a dedicated "no (further) change(s)
  needed/required" phrase pattern instead.
- **Bug**: "is physiologically plausible" wasn't recognized as a confirms-nothing-is-wrong
  phrase — the adjective list only had `relevant|indicated|appropriate|...` (no "plausible"), and
  the adverb slot only allowed "clinically" or "directly" (not "physiologically"). Fixed by
  generalizing the adverb match to any `\w+ly` and adding `plausible|typical|expected|
  unremarkable` to the adjective list.
- Hardened two other guard blocks with the same "no" exclusion for consistency, and added more
  no-issue phrasings (`nothing to flag/report/note`, `no concerns noted/identified/found`,
  `not clinically significant`, `non-issue`).

### 4. `audit_writer.py` — cross-rule-id duplicate findings (`_OVERLAPPING_RULE_GROUPS`)
Different rule-engine candidates sometimes check the same underlying fact from different angles,
and when both are genuinely true, both get confirmed and shown as if they were two separate
concerns (e.g. "male gender incompatible with pregnancy" shown twice, once under `CC.context.demo`
and once under `CC.context.conflict`). Added a **generic, reusable** mechanism: a short table of
rule-ID pairs confirmed (by live testing) to double-fire for the same fact;
`_consolidate_overlapping_findings()` keeps only the most severe finding from each group, per
section. This is deliberately NOT fuzzy text-similarity matching (too unreliable/risky — tested
and rejected that approach first) — it's a deterministic rule-ID lookup, so it never risks merging
two genuinely different findings that happen to share vocabulary.

Current table (extend this as more pairs are confirmed by testing — see note in the code):
```python
_OVERLAPPING_RULE_GROUPS = (
    frozenset({"CC.context.demo", "CC.context.conflict"}),
    frozenset({"Dx.cc.1", "Dx.cc.2"}),          # not yet observed live, added from protocol.yaml reading
    frozenset({"Dx.hs.1", "Dx.hs.2"}),          # not yet observed live, added from protocol.yaml reading
    frozenset({"Inv.demo.1", "Inv.relevance"}), # confirmed live (pregnancy ultrasound for male patient)
)
```
**How to extend**: when the user reports two findings that are clearly the same fact restated,
identify their rule_ids (cross-reference `audit_pipeline/rule_text.py` and
`audit_pipeline/rule_candidate_engine.py`'s candidate-generation code for the display text), add
the pair as a new `frozenset(...)` entry, test with synthetic data mimicking
`_normalize_findings()` calls (see commit `645084d` for the test pattern used), then confirm it
doesn't merge unrelated findings (test with two findings that share vocabulary but different
rule_ids, from an unrelated part of the rulebook).

### 5. `incremental_audit.py` — TEMPORARY diagnostic print
A `print(f"[incremental_audit] section={section} warnings={warnings!r}", flush=True)` was added
in `summarize_live_report()` to surface the real warning text into `audit_server.log` instead of
it being silently buried in the JSON response (the frontend only ever shows the generic "pipeline
notes that require review" banner, never the actual warning). **Safe to remove once no longer
needed for diagnosis** — it's marked `# DIAGNOSTIC (temporary)` in the code. A similar one exists
in `audit_writer.py`'s `write_audit_sections` for provider-call failures specifically.

## Known open items / where this was left off

1. **Fine-tune quality gap, not yet resolved**: `prescription13864/gemma-4-12b-prescription-audit`
   (the user's fine-tune, both the `-4bit` and full-precision versions) failed to catch a live
   gender/pregnancy mismatch that the base model `unsloth/gemma-4-12B-it-qat-q4_0-unquantized`
   DID catch, and that Groq's `qwen/qwen3.8-27b` also caught. Quantization was ruled out (both
   precisions of the fine-tune behaved identically). This suggests the fine-tuning process may
   have degraded general demographic/context-consistency judgment — worth investigating the
   fine-tuning dataset/approach, and worth running `benchmark/run_benchmark.py` with
   `--models prescription13864/gemma-4-12b-prescription-audit-4bit` added to compare against the
   existing `2026-10-30_model_comparison` results on the full 10/35-case test sets, not just this
   one manually-typed case.
2. **The user has not yet re-run `benchmark/run_benchmark.py`** after these fixes to get updated
   precision/recall numbers. The existing `results/2026-10-30_model_comparison/summary.csv` numbers
   predate all of the above and should not be trusted as current.
3. **The GPU-utilization / latency issue from early in the session is NOT fixed**: local audits
   run at ~50% GPU utilization and feel slow. Diagnosed (not fixed) as: no batching (one sequence
   decoded at a time — inherently decode-bound), and no KV-cache reuse across the 6 sequential
   section calls per prescription (each section re-processes the growing prescription from
   scratch). Real fix would be moving to a serving framework (vLLM/TGI) with continuous batching
   and prefix caching — a bigger project, flagged but not started. Needs the user's go-ahead
   before attempting, since it's a significant architecture change.
4. **`.env`'s `GROQ_MODEL=qwen/qwen3.8-27b`** — unusual-looking model id (not double-checked
   against Groq's actual model catalog; it appeared to work in testing so likely fine, but worth a
   second look if Groq calls ever fail with a model-not-found error).

## Workflow notes for this project

- Start everything with `.\run_local.ps1` from the project root — it reads `.env` fresh every
  time (clearing stale environment variables first), starts the backend, waits for `/health`,
  then starts the frontend. **Running `python -m audit_pipeline.app_server serve` directly in an
  existing PowerShell window can pick up STALE environment variables from earlier in that
  session** (python-dotenv loads with `override=False`), which caused real confusion earlier in
  this session — prefer `run_local.ps1`, or a brand-new terminal window, when testing an `.env`
  change.
- `audit_server.log` (stdout) and `audit_server.err.log` (stderr) in the project root are written
  by `run_local.ps1`'s redirection — useful for diagnosing without needing the user to paste
  terminal output, but only when started via `run_local.ps1` (direct `python -m ...` invocations
  don't write to these files).
- Port 8765 conflicts: if `run_local.ps1` refuses to start citing a port already in use, run
  `Get-NetTCPConnection -LocalPort 8765 -State Listen | ForEach-Object { Stop-Process -Id
  $_.OwningProcess -Force }` first.
- The user prefers plain language (jargon explained briefly), paste-ready PowerShell commands
  (not downloadable files, since they copy-paste into VS Code/terminal constantly), and wants
  direct acknowledgment when a methodology error is caught rather than it being glossed over.
- Never commit or push without being explicitly asked.
