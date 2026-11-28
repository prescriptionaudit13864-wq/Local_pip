# vLLM tuning for speed (plan task T02.2, 28 Nov 2026)

**Result:** serve with **FP8 weights** (`start_vllm.ps1 -Quant fp8`, GPU share 0.95, prefix caching on). On the
same 20-doctor workload it is **2.3× faster** (median wait per section 6.8 s → 2.9 s; slowest 5% 16.2 s → 7.5 s;
medicine section 11.5 s → 5.1 s), handles **47% more checks per minute**, uses **42% less energy**, and catches the
**same 87 of 88 errors**. The cost: about 20 more alerts in 486 checks, mostly the clinical reviewer's
"side/severity not stated" reminders.

## Method

- **Workload:** the pilot-rehearsal-2 set (`benchmark/pilot_simulation.py --scenario critical --seed 11`):
  20 doctors at once, 100 synthetic prescriptions, 88 known errors, 486 section checks (save → layer 1 → layer 2),
  3–8 s between sections. Same prescriptions, same doctors and same pauses for every setting.
- **Path:** local app (127.0.0.1:4173, doctor sign-in) → audit server → vLLM, so internet delays do not mix in.
- **Code:** branch at `25154e1` (medicine-list questions and false-alert fixes), the same for every setting.
- **Each setting:** vLLM restarted with that setting, one warm-up check, then the full workload; vLLM metrics read
  before and after (prefix-cache reuse, preemptions, KV cache size); GPU sampled every 0.5 s.
- **Answers:** every section's alerts compared with setting A (current) and judged against the known errors with
  the strict scoring of `pilot_simulation.score` (right line **and** right kind of alert).
- Scripts: `benchmark/vllm_tuning_compare.py`; raw results: `benchmark/results/tuning/`.

## Settings tested

| | Weights | GPU share | KV cache | Prefix cache | Request limit |
|---|---|---|---|---|---|
| A current | bf16 | 0.88 | bf16 | on | default |
| B | bf16 | 0.95 | bf16 | on | default |
| C | bf16 | 0.95 | bf16 | **off** | default |
| **D** | **FP8** | 0.95 | bf16 | on | default |
| E | FP8 | 0.95 | **FP8** | on | default |
| F | FP8 | 0.95 | bf16 | on | **32** |

A is what was actually running: `start_vllm.ps1` passed `GpuUtil 0.88`, which overrode the 0.95 default of
`start_vllm.sh`. Fixed: the Windows launcher now defaults to 0.95.

## Speed (what one doctor waits, both layers per section)

| | Median | 95% within | Slowest | Over 6 s | Over 10 s | Medicine section median / 95% | Checks per minute |
|---|---|---|---|---|---|---|---|
| A current | 6.8 s | 16.2 s | 27.8 s | 285 | 117 | 11.5 / 21.5 s | 79 |
| B | 5.4 s | 13.4 s | 26.8 s | 205 | 73 | 8.4 / 19.4 s | 88 |
| C (no prefix cache) | 6.7 s | 16.3 s | 28.2 s | 274 | 108 | 10.9 / 24.5 s | 80 |
| **D FP8** | **2.9 s** | **7.5 s** | **13.4 s** | **55** | **8** | **5.1 / 11.1 s** | **116** |
| E FP8 + FP8 KV | 2.7 s | 7.0 s | 12.5 s | 45 | 5 | 5.2 / 10.0 s | 120 |
| F FP8, limit 32 | 3.0 s | 8.2 s | 13.8 s | 56 | 13 | 5.3 / 11.5 s | 115 |

Per layer (median / 95%): A layer 1 3.0 / 11.5 s, layer 2 3.0 / 7.2 s; D layer 1 1.1 / 4.6 s, layer 2 1.1 / 3.9 s.
0 failed checks in every setting (486 of 486).

## Why it is faster

| | KV cache (relative to A) | Prefix-cache reuse | Requests pushed out (preemptions) |
|---|---|---|---|
| A current | 1.0× | 15.6% | 49 |
| B | 1.5× | 17.5% | 13 |
| C (no prefix cache) | 1.5× | 0% | 31 |
| D FP8 | **6.4×** | **24.4%** | **0** |
| E FP8 + FP8 KV | 12.9× | 24.6% | 0 |
| F FP8, limit 32 | 7.0× | 24.4% | 0 |

- **Memory was the bottleneck.** bf16 weights take ~24 GB, leaving little room to hold the requests in progress
  (the KV cache). When it fills, vLLM pushes requests out and recomputes them later (49 times in A). FP8 weights
  take ~12 GB, so the KV cache is 6.4× larger and nothing is pushed out.
- **Prefix caching helps (C vs B: 6.7 s vs 5.4 s)** and helps more with room to keep the shared rule text (24% of
  prompt tokens reused with FP8 vs 16% in A). It is on by default in vLLM 0.34; keep it on.
- **A request limit of 32 does not help** (F vs D): there is no queue to control once memory is sufficient.
- **FP8 KV cache (E)** is slightly faster still but loses accuracy (below).

## Answers: does the setting change the alerts?

| | Errors caught (strict) | Layer 1 alerts same as A | Layer 2 same as A | Alerts on error-free lines |
|---|---|---|---|---|
| A current | 87 / 88 | — | — | 31 |
| B | 85 / 88 | 482 / 486 (99.2%) | 484 / 486 (99.6%) | 32 |
| C | 87 / 88 | 482 / 486 (99.2%) | 484 / 486 (99.6%) | 32 |
| **D FP8** | **87 / 88** | 473 / 486 (97.3%) | 467 / 486 (96.1%) | 49 |
| E FP8 + FP8 KV | 84 / 88 | 466 / 486 (95.9%) | 467 / 486 (96.1%) | 39 |
| F FP8, limit 32 | 86 / 88 | 471 / 486 (96.9%) | 466 / 486 (95.9%) | 49 |

B and C use the same weights as A; their ~1% differences are the run-to-run variation of batching (requests
grouped differently give slightly different numbers), so differences of about 1% are noise.

Every alert that differs from A, judged against the known errors:

| | Added: right | Added: repeat of a real error | Added: side/severity reminder | Added: false | Lost: right |
|---|---|---|---|---|---|
| B | 1 | 2 | 0 | 1 | 5 |
| **D FP8** | 3 | 5 | **16** | **4** | 4 |
| E FP8 + FP8 KV | 3 | 8 | 12 | 4 | **8** |
| F FP8, limit 32 | 2 | 7 | 17 | 4 | 5 |

- **D catches the same errors as A** (87/88; it gained 3 and lost 4 individual alerts, within noise for 88 errors).
- **D's main change is more layer-2 reminders** ("side of the ear not stated", "severity not stated"): +16. The 4 new
  false alerts are habits already seen at bf16 ("headache but hypertension not in complaints", "required medicine
  missing" for a heart attack).
- **E is rejected:** it lost 8 correct alerts (84/88).

## GPU load and energy

| | GPU busy (mean) | Power mean / peak | Energy for the workload | Wall time |
|---|---|---|---|---|
| A current | 94% | 316 / 364 W | 32.5 Wh | 370 s |
| B | 94% | 322 / 368 W | 29.5 Wh | 330 s |
| **D FP8** | 89% | **271 / 339 W** | **19.0 Wh (−42%)** | 252 s |
| E | 92% | 276 / 339 W | 18.7 Wh | 244 s |

All under the 400 W cap. FP8 does less memory traffic per token, so it draws less power and finishes sooner.

## Decision

1. **Use D: FP8 weights, GPU share 0.95, prefix caching on, default request limit.** Now the default in
   `deploy/vllm/start_vllm.ps1` (`-Quant fp8`); `-Quant ""` returns to bf16.
2. **Fixed:** the Windows launcher's GPU share 0.88 → 0.95 (it silently overrode the Linux script).
3. **Not used:** FP8 KV cache (loses correct alerts), request limit (no gain).
4. **Before the real pilot:** re-run the equivalence check (`benchmark/vllm_equivalence.py`, 100 team test checks)
   and the live suite on FP8, and watch the extra side/severity reminders in doctor feedback.

## Limits

- One run per setting; batching makes runs vary by about 1% in alerts. A second run of A and D would give the
  noise band exactly.
- Synthetic prescriptions and a compressed pace (3–8 s between sections; real doctors take minutes), so absolute
  waits are higher than in real use; the ranking of settings is what matters.
