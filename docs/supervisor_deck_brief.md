# Brief for Claude: build a supervisor presentation

**Instructions to Claude:** Build a 14-slide presentation from this brief for a university research supervisor.
Style: academic and professional (clean white background, one accent colour, sans-serif, numbered slides, a
small source line under every chart or table). **Show, do not write:** each slide is led by a chart, table or
diagram; text is limited to a title that states the finding and at most 3 short bullet points. Use exactly the
numbers below; do not invent data. Draw charts from the data tables given (bar, grouped bar, line, donut as
indicated). Draw the diagrams as clean flow or architecture diagrams. Put the "Source" line in small grey text.

**Title:** PrescribeX: Serving a Two-Layer Prescription-Audit Model with vLLM for Multi-Doctor Use
**Subtitle:** Deployment, equivalence, load and pilot-readiness evaluation
**Presenter:** Tanim · **Date:** 24 November 2026 · **Project:** PrescribeX (prescription audit)

---

## Slide 1 — Title slide

Title, subtitle, presenter, date. A small illustration: a prescription sheet with a check mark and a GPU chip.

## Slide 2 — Research question and objectives

Diagram (three boxes left to right, arrows):
**Can one local GPU serve many doctors at once** → **without changing the model's answers** → **at a wait a doctor
accepts?**

Table: objectives and measures

| Objective | Measure | Target |
|---|---|---|
| O1 Serve both model layers on one server | vLLM runs base model + fine-tuned adapter | Both layers answer |
| O2 Same answers as the current engine | Share of checks with identical alerts | ≥ 95% |
| O3 Many doctors at once | Checks per minute, wait per check | Wait < 6 s with 10+ doctors |
| O4 Ready for a real pilot | Simulated pilot: failures, errors caught, GPU load | 0 failures, power < 400 W |

## Slide 3 — The model: two layers

Architecture diagram (top to bottom):
Doctor writes a section → **Layer 1: fast check** (Gemma 4 12B + PrescribeX v5 LoRA adapter, trained on audit
rules) → alerts → **Layer 2: clinical reviewer** (same base model, adapter off, reads drug facts) → second-opinion
alerts → shown to the doctor.

Bullets (max 3): the model decides every alert, no hand-written alert rules · same base weights serve both
layers · runs fully on one local RTX 5090 (32 GB).

## Slide 4 — Before and after: serving architecture

Two side-by-side diagrams.

**Before (local engine):** Browser → app → audit server (Windows) → transformers in-process, 4-bit weights,
**one request at a time behind a GPU lock** (draw a queue of waiting requests).

**After (vLLM):** Browser → app → audit server → **vLLM server (WSL2)**: base model loaded once in bf16, adapter
selected per request (`prescribex` = layer 1, base name = layer 2), **continuous batching** (draw many requests
sharing one GPU step).

Table under the diagrams:

| | Local engine | vLLM |
|---|---|---|
| Weights | 4-bit | bf16 (16-bit) |
| Concurrency | 1 request at a time | Batched, many at once |
| GPU memory | 11.5 GB | 29.5 GB (reserved at start) |
| Platform | Windows, in-process | WSL2 Ubuntu, vLLM 0.34.0 |

## Slide 5 — Deployment problems solved

Table (no chart):

| Problem found | Cause | Fix |
|---|---|---|
| Crash at start (`num_soft_tokens`) | vLLM 0.34 reads a vision setting this checkpoint lacks | Text-only mode (`--language-model-only`) |
| Kernel compile failed | No C compiler in WSL | `build-essential` in the install script |
| KV cache too small for one 8K request | GPU share 0.88 | GPU share 0.95 → 14,621-token KV cache |
| Was the LoRA really applied? | Known vLLM bug in older versions | Same prompt: trained JSON on adapter, different text on base ✔ |
| Server stopped on its own | WSL shuts down with no open terminal | Start script keeps a window open |

## Slide 6 — Equivalence: same answers as before (O2)

**Grouped bar chart:** "Share of section checks with identical alerts, vLLM vs local engine" (100 team-written
test checks). Draw a dashed line at the 95% target.

| Layer | Identical alerts |
|---|---|
| Layer 1 — fast check | 100% (100/100) |
| Layer 2 — clinical reviewer | 94% (94/100) |

**Small table:** the 6 layer-2 differences

| Direction | Count | Examples |
|---|---|---|
| vLLM caught, local missed | 4 | Pregnancy in a male; gestational diabetes in a male; weight loss for 1 day |
| Local caught, vLLM missed | 2 | PSA test for a woman; adult paracetamol dose for a 4-year-old |

Bullet: differences come from 16-bit vs 4-bit weights, not from the server; they go both ways.
Source: `docs/vllm_equivalence_report.md`.

## Slide 7 — Speed: one doctor

**Bar chart:** "Median wait per section check (seconds), lower is better"

| | Local engine | vLLM |
|---|---|---|
| Layer 1 | 2.09 | 0.61 |
| Layer 2 | 2.78 | 0.64 |

Callout: **3.4–4.3× faster**. Source: `docs/vllm_equivalence_report.md`.

## Slide 8 — Scaling: many doctors at once (O3)

**Line chart, two y-axes or two small charts:** x = doctors at once (1, 4, 8, 16).

| Doctors at once | vLLM checks/min | vLLM layer-1 median wait (s) | Local checks/min | Local layer-1 median wait (s) |
|---|---|---|---|---|
| 1 | 25 | 0.7 | 6.7 | 2.0 |
| 4 | 63 | 1.0 | 7.7 | 12.8 |
| 8 | 98 | 1.3 | — | — |
| 16 | 116 | 2.8 | — | — |

Callout: with 4 doctors the local engine makes each wait **12.8 s**; vLLM **1.0 s**. vLLM throughput grows
**4.6×** from 1 to 16 doctors with 0 errors. Source: `docs/vllm_load_report.md`, `docs/engine_audit_report.md`.

## Slide 9 — Head-to-head audit: 10 synthetic cases

**Table with coloured cells (green = better):**

| Metric | Local engine | vLLM |
|---|---|---|
| Real errors caught (either layer) | 8 / 8 | 8 / 8 |
| Layer 1 caught | 7 / 8 | 8 / 8 |
| False alerts | 3 | 4 |
| Total time, 26 checks | 202 s | 55 s |
| GPU busy (mean) | 43% | 94% |
| Power (mean) | 118 W | 274 W |
| Energy for the 10 cases | 6.6 Wh | 4.2 Wh |

**Small bar chart:** energy per run (6.6 vs 4.2 Wh). Bullet: vLLM works the GPU harder but finishes sooner, so it
uses **36% less energy** for the same work. Source: `docs/engine_audit_report.md`.

## Slide 10 — Pilot rehearsal design (O4)

Flow diagram: **10 simulated doctors** (each: own access code + profile) → **public HTTPS link (Cloudflare
Tunnel)** → **sign-in gate** → app (save each section) → audit server → **vLLM** (layer 1 → layer 2).

Table:

| Design item | Value |
|---|---|
| Doctors at once | 10 |
| Prescriptions | 50, all different (5 per doctor) |
| Clean / with errors | 15 / 35 |
| Known errors | 44, in 10 rule types |
| Pace | 3–8 s between sections (real: 1–3 min) → a stress test |
| Data | Synthetic only, removed after the test |

## Slide 11 — Pilot rehearsal: reliability and wait

**Horizontal bar chart:** "Median wait per section (both layers), per doctor" — 10 bars:
3.1, 3.5, 3.3, 3.1, 3.6, 3.1, 3.6, 3.5, 3.0, 3.2 s (Sim Doctor 01–10). Dashed line at 6 s (design limit).

**Latency table:**

| Step | Median | 95th percentile | Slowest |
|---|---|---|---|
| Save section | 0.18 s | 0.30 s | 0.50 s |
| Layer 1 | 1.35 s | 3.71 s | 5.77 s |
| Layer 2 | 1.56 s | 4.30 s | 6.28 s |

Big-number callouts: **252 checks · 0 failures · 56 checks/min**. Source: `docs/pilot_simulation_report.md`.

## Slide 12 — Pilot rehearsal: errors caught

**Horizontal stacked or grouped bar chart:** "Errors caught by type" (caught vs missed).

| Error type | Caught | Total |
|---|---|---|
| Complaint with no duration | 8 | 8 |
| History longer than patient's life | 8 | 8 |
| Sex-specific history | 7 | 7 |
| Sex-specific complaint | 4 | 4 |
| Test not fitting the sex | 4 | 4 |
| Referral missing (heart attack) | 3 | 3 |
| Adult dose for a child | 2 | 2 |
| Diagnosis not fitting the sex | 2 | 2 |
| Medicine without matching condition | 2 | 3 |
| Required medicine missing (scabies) | 0 | 3 |

**Donut chart:** overall **91% caught (40/44)**. Small table: layer 1 39/44 (89%) · layer 2 9/44 (20%) · either
40/44 (91%). Bullet: the only type fully missed is a known weak type (required treatment missing), already in
the data plan.

## Slide 13 — False alerts and GPU load

Left: **bar chart** "False alerts by kind" (29 over ~209 error-free sections)

| Kind | Layer | Count |
|---|---|---|
| Side not stated | 2 | 12 |
| Severity not stated | 2 | 7 |
| Hygiene/food advice missing | 1 | 4 |
| Meal timing for an inhaler | 1 | 3 |
| Other | 1–2 | 3 |

Right: **gauge or bar** "GPU during the rehearsal" against the 400 W cap

| | Value |
|---|---|
| GPU busy, mean / peak | 84% / 100% |
| Power, mean / peak | 266 W / 344 W (cap 400 W) |
| Memory | 29.7 GB of 32 GB |
| Energy | ≈ 0.4 Wh per prescription |

Bullet: the main risk is false alerts (mostly layer-2 documentation reminders), not capacity.

## Slide 14 — Conclusions and next steps

**Objectives scorecard table (with ✔ / ~ icons):**

| Objective | Result | Status |
|---|---|---|
| O1 Both layers on one vLLM server | Base + LoRA served, adapter verified | ✔ |
| O2 Same answers (≥ 95%) | Layer 1 100%, layer 2 94% | ~ (layer 2 just under; differences both ways) |
| O3 Many doctors | 16 doctors: 116 checks/min, 2.8 s; local engine 12.8 s at 4 | ✔ |
| O4 Pilot-ready | 10 doctors, 0 failures, 91% caught, peak 344 W | ✔ |

**Timeline graphic (4 steps):** Supervised pilot with 10 doctors → collect doctor feedback on every alert →
tune layer-2 "side/severity" reminders and add required-treatment knowledge → re-test layer-2 bf16 vs 4-bit on
the gold set.

Small footnote: Cloudflare Tunnel relays the traffic (needs project-lead approval before real patients);
records stay only on the local PC.

---

**Sources (all in the project repository):** `docs/vllm_serving_plan.md`, `docs/vllm_equivalence_report.md`,
`docs/vllm_load_report.md`, `docs/engine_audit_report.md`, `docs/pilot_simulation_report.md`,
`docs/weak_error_types_data_request.md`.
