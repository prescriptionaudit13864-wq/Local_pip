# Weekly progress: serving PrescribeX to many doctors (Tanim, W01 22–27 Nov → W02 start, 28 Nov 2026)

Plan tasks: T01.1–T01.3 (vLLM serving, equivalence), T02.1 (load test), T02.2 (tune for speed), T02.5 (remote
access), plus pilot readiness (doctor sign-in, doctor-wise records, false-alert fixes).

## The week in one table

All runs scored the same way (`benchmark/pilot_simulation.score`, strict: right line and right kind of alert;
side/severity reminders that follow the clinic's documentation standard are counted as correct, not false). "Wait" is
what a doctor waits per section for both checks (fast check + clinical reviewer), median / 95% within.

| Date | Step | Doctors at once | Wait per section | Medicine section | Checks / min | Errors caught | False alerts | Clean prescriptions with no alert | Failures |
|---|---|---|---|---|---|---|---|---|---|
| 23 Nov | Start: local engine (4-bit, one request at a time) | 4 | 12.8 s (fast check alone) | — | 7.7 | — | — | — | 0 |
| 23 Nov | vLLM serving (bf16) | 16 | 2.8 s (fast check alone) | — | 116 | same alerts as local: layer 1 100%, layer 2 94% | — | — | 0 |
| 24 Nov | Rehearsal 1 over the pilot link | 10 | 3.3 / 6.7 s | 3.2 s | 56 | 39 / 44 | 29 | 10 / 15 | 0 |
| 24 Nov | Rehearsal 2: common critical errors | 20 | 5.3 / 12.0 s | 8.1 s | 93 | **56 / 88** | 52 | 14 / 25 | 0 |
| 25 Nov | Medicine-list checks (`25154e1`) | 20 | 6.8 / 16.2 s | 11.5 s | 79 | **87 / 88** | 12 | 24 / 25 | 0 |
| 28 Nov | **Speed tuning: FP8 weights (T02.2)** | 20 | **2.9 / 7.5 s** | **5.1 s** | **116** | 87 / 88 | 30 | 19 / 25 | 0 |
| 28 Nov | 30 doctors, before tuning | 30 | 12.2 / 24.5 s | 18.4 s | 85 | 127 / 130 | 24 | 35 / 38 | 0 |
| 28 Nov | 30 doctors, after tuning | 30 | **4.1 / 9.8 s** | **6.0 s** | **152** | 126 / 130 | 50 | 27 / 38 | 0 |
| 28 Nov | **30 doctors, tuning + false-alert fixes + doctor-wise tracking** | 30 | 4.4 / 10.8 s | 6.2 s | 148 | **126 / 130** | **19** | **35 / 38** | **0** |

The 25 Nov and 28 Nov 20-doctor rows use the same 100 prescriptions (88 errors); the 30-doctor rows use the same 150
prescriptions (130 errors). The last row also includes doctors fixing alerts and re-checking (838 checks instead of
735), so its wait is measured under more load.

## 1. Speed: how the tuning was done (T02.2)

| Step | What was found | What was done | Effect (20 doctors) |
|---|---|---|---|
| 1 | vLLM ran with GPU share 0.88, not 0.95: `start_vllm.ps1` overrode `start_vllm.sh` | Launcher default 0.95 | KV cache 1.5×, pushed-out requests 49 → 13; wait 6.8 → 5.4 s |
| 2 | Prefix caching was on by default, but only 16% of prompt tokens were reused: the KV cache was too small to keep the shared rule text | Kept on (measured: off is 24% slower) | — |
| 3 | bf16 weights (24 GB) left little memory for requests in progress | **FP8 weights** (12 GB): KV cache 6.4× larger, 0 pushed-out requests, 24% prefix reuse | **wait 6.8 → 2.9 s, medicine 11.5 → 5.1 s, 79 → 116 checks/min, −42% energy**, same 87/88 errors |
| 4 | FP8 KV cache: slightly faster but lost 8 correct alerts | Rejected | — |
| 5 | Request limit 32: no gain once memory was enough | Rejected | — |
| 6 | 30-doctor bursts: 3 checks failed with "502 Bad Gateway" | The audit server's listen queue was 5 connections: raised to 128 | 0 failures in the final run |

Under 30 doctors, the tuning cut the wait per section from 12.2 to 4.1 s, the slowest check from 126 to 17 s, and a
doctor's 5 prescriptions from 7.8 to 4.1 minutes (`docs/pilot30_report.md`). The final check pressed by all 30 at
once: median 44 → 30 s.

## 2. False alerts: what was wrong and what was fixed today

FP8 doubled the clinical reviewer's false alerts (bf16 24 → FP8 50). Looked at one by one (30-doctor tuned run):

| False alert | Count | Cause | Fix (no hand-written alert rules) |
|---|---|---|---|
| "Side not stated" for scabies, itching, peptic ulcer, a stroke | 21 | The reviewer labelled any location question "side_missing" | A "side" problem must name a body part that exists more than once and not already state the side (the type's own definition); otherwise it is relabelled and hidden with the unreliable types (`clinical_reviewer.side_once`) |
| "Side not stated" for "weakness of **right** side" | 2 | Side already written | Same check |
| "Severity not stated" for an ordinary headache | 9 | The second look answered a bare list of numbers and kept it, against the standard it was given | Second look now gives a reason first, then KEEP/DROP per problem (`clinical_reviewer.VERIFY`, as the fast check's second opinion already did) |
| "Instructions not written" for a cream with "Apply once at night" | 2 | Fast-check rule `Rx.instr` | Added to the fast check's second opinion (`FINETUNE_VERIFY_RULES`) |
| Scoring: duplicate alerts labelled `Rx.dup.class`; a medicine without reason from a duplicate pair; documentation reminders that follow the standard | — | My scoring, not the model | Fixed in `pilot_simulation.score` |

Result on the same 150 prescriptions: **false alerts 50 → 19; clinical-reviewer false alerts 32 → 0; clean
prescriptions with no alert 27 → 35 of 38; errors caught unchanged (126/130; 128 in a repeat run).**

Still open (honest):
- **11 "required medicine missing"** for a heart attack treated with aspirin only, or a stroke with amlodipine only.
  Clinically defensible; the answer key does not list them. Needs Dr. Israt's decision.
- **6 "hypertension not in the complaints"** for a headache visit (`Dx.cc.1`), 1 hygiene advice, 1 investigation.
- **Misses:** naproxen with a peptic-ulcer history 2 of 7, PSA for a woman 2 of 3 (they vary between runs: 0–2).

## 3. Doctor-wise records and what doctors do with the alerts

Records are stored per doctor (`prescribex-app/backend/data/`):

```
doctor_<id>_<Name>/                     one folder per signed-in doctor (renamed if the doctor changes their name)
  patient_<Patient ID>_<Name>/          the doctor's patients
    patient.json                        details and every change to them
    visit_NN/                           one prescription
      <NN>_<section>.json               latest version of each section
      history/<section>.json            every save (what changed), every check with its errors and fixes,
                                        the reviewer's problems, which errors a later edit resolved
      alerts.json                       NEW: every alert of this prescription and what the doctor did
```

New today, after a critical look at the tracking:

| Gap | Fix |
|---|---|
| An alert the doctor ignored was not recorded | `alerts.json` outcome **not acted on** |
| "Fever" → "High fever" with the alert still there counted as resolved | Outcome **edited, still flagged** (the same rule still on the edited line) |
| An alert that was simply not raised again counted as fixed | Fixed only when the doctor changed, removed or (for something missing, e.g. a referral) added a line; otherwise **gone on recheck, line not edited** |
| Alert lines and saved rows are written differently ("Seclo 20 mg [Before meal]" vs "Seclo \| 20 mg \| Before meal") | Lines compared by their words; medicine rows by name |

Tested with 30 doctors who act on 80% of correct alerts (fix, re-save, re-check) and then save the prescription:

| Check | Result |
|---|---|
| Doctor folders | 30 / 30, each with its own 5 patients (150 patients, 150 prescriptions, 150 final saves) |
| Alerts shown | 256 |
| Fixed by the doctor | 146 (from 108 doctor edits: one edit can clear two alerts, e.g. dose and duration of one line) |
| Edited but still flagged | 4 (e.g. Thyrox frequency filled in, but Thyrox still has no reason in the prescription: correct) |
| Not acted on | 106 (the 20% of correct alerts the simulated doctors skipped, documentation reminders, and false alerts) |
| After a fix, did the re-check clear the alert? | 107 of 108 |

In the real pilot, `alerts.json` gives the measure the supervisor will ask for: how often doctors act on alerts, per
doctor and per alert type.

## 4. Where we are against the plan

| Task | Status |
|---|---|
| T01.1–T01.3 vLLM serving, provider, equivalence | Done (PR #2) |
| T01.5 CMED concept note + NDA | Drafted (sending: Tanim) |
| T02.1 Load test | Done: 1–30 doctors, section checks and final check |
| T02.2 Tune for speed | **Done**: FP8 default, tuning report |
| T02.3 Scalability report | Material ready (this report, `vllm_tuning_report.md`, `pilot30_report.md`); cost per prescription still to add |
| T02.5 Remote access for the pre-pilot | Done ahead of plan: Cloudflare link, doctor sign-in, profiles, doctor-wise records |
| Next | T03.1 API contract, T03.2 "never block the doctor" (timeout + fallback), cost per prescription for T02.3; decision on the open false alerts with Dr. Israt |

Sources: `docs/vllm_equivalence_report.md`, `docs/vllm_load_report.md`, `docs/engine_audit_report.md`,
`docs/pilot_simulation_report.md`, `docs/pilot_simulation_20_report.md`, `docs/vllm_tuning_report.md`,
`docs/pilot30_report.md`; raw results in `benchmark/results/`.
