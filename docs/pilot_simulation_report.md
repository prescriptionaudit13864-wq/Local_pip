# Pilot rehearsal: 10 doctors, 50 prescriptions, vLLM through the Cloudflare link (24 Nov 2026)

> **Correction (rehearsal 2, `docs/pilot_simulation_20_report.md`):** with strict scoring (an alert must also be the
> right kind of alert), this test caught **39 of 44 (89%)**, not 40 of 44. Other conclusions are unchanged.

**Question:** before 10 real doctors use PrescribeX from their own computers, does the pipeline hold up, how
long do doctors wait, how many errors are caught, and how hard is the GPU working?

**Answer:** yes. 10 doctors at once, 252 section checks, **0 failures**. A doctor waited a median of
**about 3 s** per section for both layers. The system caught **40 of 44 errors (91%)**; the 4 misses are a
type the model is already known to miss. The GPU was **84% busy on average**, peak **344 W** (cap 400 W),
memory 29.7 GB of 32 GB.

## How the test was run

| | |
|---|---|
| Path | The real pilot path: public Cloudflare link → doctor sign-in → app server → audit server → vLLM (WSL2) |
| Doctors | 10 test doctors, each with their own access code, profile and cookie session, working at the same time (started 1 s apart) |
| Prescriptions | 50, all different, 5 per doctor (`benchmark/pilot_simulation.py`, seed 7). 15 clean, 35 with 1–2 known errors (44 errors in all) |
| Errors | Built from the error types in the model's rule list: sex-specific complaints, history, investigations and diagnoses; missing durations; history longer than the patient's life; adult doses for children; medicines without a matching condition; referral missing for a heart attack; required medicine missing (scabies with no scabicide) |
| Each section | Save the step → layer 1 (fine-tuned fast check) → layer 2 (clinical reviewer), exactly as the app does |
| Pace | 3–8 s between sections. A real doctor takes 1–3 minutes per section, so this is a **much heavier load** than 10 real doctors |
| Model | Gemma 4 12B + v5 adapter, bf16, vLLM 0.34, `FINETUNE_LINE_ANSWERS_ADD=1` |
| Data | Synthetic only. Test doctors, their records and their Excel rows were removed afterwards |

Raw results: `benchmark/results/pilot_simulation.json`.

## 1. Did it work for every doctor?

| Doctor | Section checks | Alerts (layer 1 / layer 2) | Median wait per section (both layers) | Failures |
|---|---|---|---|---|
| Sim Doctor 01 | 25 | 5 / 5 | 3.1 s | 0 |
| Sim Doctor 02 | 25 | 9 / 3 | 3.5 s | 0 |
| Sim Doctor 03 | 26 | 5 / 2 | 3.3 s | 0 |
| Sim Doctor 04 | 25 | 8 / 8 | 3.1 s | 0 |
| Sim Doctor 05 | 25 | 5 / 1 | 3.6 s | 0 |
| Sim Doctor 06 | 24 | 5 / 7 | 3.1 s | 0 |
| Sim Doctor 07 | 26 | 8 / 7 | 3.6 s | 0 |
| Sim Doctor 08 | 25 | 10 / 6 | 3.5 s | 0 |
| Sim Doctor 09 | 24 | 3 / 1 | 3.0 s | 0 |
| Sim Doctor 10 | 27 | 7 / 2 | 3.2 s | 0 |
| **All** | **252** | **65 / 42** | **≈3.3 s** | **0** |

All 10 doctors signed in, saved every section and got both layers back. No timeouts or errors.

## 2. How long did doctors wait?

| Step | Median | 95% of checks within | Slowest |
|---|---|---|---|
| Save the section | 0.18 s | 0.30 s | 0.50 s |
| Layer 1 – fast check | **1.35 s** | 3.71 s | 5.77 s |
| Layer 2 – clinical reviewer | **1.56 s** | 4.30 s | 6.28 s |

Throughput: 252 section checks (each with both layers) in 271 s = **56 checks per minute**. For comparison, one
doctor alone waits 0.7 s for layer 1 (`docs/engine_audit_report.md`): 10 busy doctors roughly double the wait,
and it stays well under the 6 s the app is designed for. Times include the Cloudflare round trip.

## 3. How many errors were caught?

| | Caught | Share |
|---|---|---|
| Layer 1 (fast check) | 39 of 44 | 89% |
| Layer 2 (clinical reviewer) | 9 of 44 | 20% |
| **Whole system (either layer)** | **40 of 44** | **91%** |
| Prescriptions with errors where every error was caught | 31 of 35 | 89% |
| Clean prescriptions with no alert at all | 10 of 15 | 67% |

By error type:

| Error type (rule) | Errors | Layer 1 | Layer 2 | Caught |
|---|---|---|---|---|
| Complaint with no duration (`CC.3`) | 8 | 8 | 0 | **8/8** |
| History longer than the patient's life (`Hx.time.plausibility`) | 8 | 8 | 0 | **8/8** |
| Sex-specific history (`Hx.demo`) | 7 | 7 | 1 | **7/7** |
| Sex-specific complaint (`CC.demo`) | 4 | 4 | 1 | **4/4** |
| Test that does not fit the sex (e.g. PSA for a woman) | 4 | 3 | 3 | **4/4** |
| Medicine without a matching condition (`Rx.over`) | 3 | 2 | 0 | 2/3 |
| Referral missing for a heart attack (`Adv.refer`) | 3 | 3 | 0 | **3/3** |
| Required medicine missing: scabies, no scabicide (`Rx.cc.sym`) | 3 | 0 | 0 | **0/3** |
| Adult dose for a child (`Rx.demo`) | 2 | 2 | 2 | **2/2** |
| Diagnosis that does not fit the sex (`Dx.demo`) | 2 | 2 | 2 | **2/2** |

The 4 misses: the 3 scabies cases (`Rx.cc.sym`, caught 0 of 7 on validation too: the model has no list of
treatments a diagnosis requires; `docs/weak_error_types_data_request.md`, decision 3) and one thyroid medicine
given for back pain (`Rx.over`).

Layer 1 does most of the catching. Layer 2 adds safety on the serious ones (child doses, wrong-sex tests and
diagnoses) and repeats real errors from other sections (18 times), but it caught only one error that layer 1
missed (a PSA test for a woman).

## 4. False alerts

29 alerts on lines without an error, over about 209 error-free sections:

| Alert | Layer | Count | Judgement |
|---|---|---|---|
| "Side not stated" (ear pain, back pain) | 2 | 12 | Documentation reminder, not an error; the most common annoyance |
| "Severity not stated" (headache, abdominal pain, breathlessness) | 2 | 7 | Arguably useful for chest pain; noise for routine visits |
| "Hygiene/food advice missing" (`Adv.cc`) | 1 | 4 | Model habit seen before |
| "Before/after meal timing missing" for an inhaler (`Rx.timing`) | 1 | 3 | Wrong: timing does not apply to inhalers |
| Other | 1–2 | 3 | One-offs |

Not counted as false: 18 alerts that point at a real error from another section (e.g. "vaginal discharge in a
male" raised again under investigations), and 9 about lines this test left in by mistake (an HbA1c test left in
the heart-attack and scabies visits, which the model fairly questioned).

Two-thirds of the false alerts come from layer 2's "side/severity not stated". If doctors find them tiresome, that
is the first thing to tune in the reviewer prompt.

## 5. GPU load

| | During the test |
|---|---|
| GPU busy | mean **84%**, peak 100% |
| Power | mean **266 W**, peak **344 W** (cap 400 W) |
| Memory | 29.7 GB of 32 GB (vLLM reserves it at start; it does not grow with more doctors) |
| Energy | about 20 Wh for the whole test, **≈0.4 Wh per prescription** |

The GPU was busy most of the time because the pace was compressed. With real doctors (1–3 minutes per section)
it will be idle most of the time and only work in short bursts. Power stayed below the 400 W cap throughout.

## Is the pipeline ready for 10 real doctors?

**Yes, for a supervised pilot.**
- **Capacity:** no failures, and the wait stayed around 3 s at a pace much faster than real use. The earlier load
  test reached 116 checks per minute with 16 doctors at once (`docs/vllm_load_report.md`).
- **Accuracy:** 91% of errors caught across the trained types; the misses are the known weak type
  (required medicine missing).
- **Main risk is false alerts, not capacity:** 5 of 15 clean prescriptions got at least one alert, mostly layer 2
  asking for side or severity.

Before the real pilot:
1. Tell the doctors that alerts are suggestions, and that "side/severity not stated" is a reminder.
2. Keep the pilot PC on, awake, online and on mains power; nothing else on the GPU during the pilot.
3. The link changes at each start (free Cloudflare quick tunnel): send it to the doctors each pilot day.
4. Collect the doctors' feedback on each alert (useful or not). That is the real-world accuracy measure, and it
   feeds the next model.
5. The privacy trade-off of Cloudflare (it can read the traffic while relaying it) needs the project lead's OK
   before real patients.

## Limits of this test

- Synthetic prescriptions, written from templates by the same team that wrote the expected answers; real
  prescriptions are messier. The real-prescription figure is 79% caught (`docs/model_register.md`).
- 44 errors in 10 types; some types have only 2–3 cases.
- Answers are matched by the error line's text appearing in an alert; a few edge cases were checked by hand.
- During the run, one real save (Tanim, 15:07) happened at the same time; its Excel rows were restored afterwards
  from the saved files. Its fast-check result rows (stored only in Excel) could not be restored.
