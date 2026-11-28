# Pilot rehearsal 2: 20 doctors, 100 prescriptions with common critical errors (24 Nov 2026)

**Question:** with the new medicine-schedule check (commit 01b482c), can the pipeline serve 20 doctors at once, and
how well does it catch the errors doctors most often make when writing a prescription?

**Short answer:**

| | Result |
|---|---|
| Reliability | 486 section checks from 20 doctors at once, **0 failures** |
| Wait per section (both layers) | median **5.3 s**, 95% within 12 s (heavier than real use, see pace) |
| Errors caught | **56 of 88 (64%)**: near-perfect on missing information, weak on medicine judgement |
| Clean prescriptions with no alert | 14 of 25 (56%) |
| GPU | 94% busy, mean 311 W, **peak 360 W** (cap 400 W), 29.7 GB |

**Main finding:** the system reliably catches what is *missing* from a prescription (dose/frequency, duration,
complaint duration, referral in an emergency) but rarely catches *wrong choices of medicine* (duplicate drugs,
antibiotics without a reason, a drug that conflicts with the history, drug interactions). Those need model
training or knowledge-base work, not server capacity.

## 1. Setup

| | |
|---|---|
| Path | Real pilot path: public Cloudflare link → doctor sign-in → app (saves each section) → audit server → vLLM |
| Code | clean-v1 with 01b482c (medicine-schedule questions) + a fix found in this test (below) |
| Doctors | 20 test doctors, each with own access code, profile and session, started 1 s apart |
| Prescriptions | 100, all different (`benchmark/pilot_simulation.py --scenario critical --seed 11`), 5 per doctor |
| Clean / with errors | 25 / 75; **88 errors** (25% of erroneous prescriptions have two) |
| Pace | 3–8 s between sections. A real doctor takes 1–3 min per section: **this load is many times heavier than 20 real doctors** |
| Data | Synthetic. Test doctors, records and their 286 Excel log rows were removed afterwards; real doctors' data untouched |

The error mix is weighted toward what doctors commonly do:

| Error (rule) | Example | Count |
|---|---|---|
| Medicine with no dose/frequency (`Rx.freq.missing`) | `Ace 500 mg`, nothing after the strength | 14 |
| Complaint with no duration (`CC.3`) | "Dry cough" | 14 |
| Antibiotic with no infection (`Rx.over`) | Cefixime for back pain | 14 |
| Medicine with no duration (`Rx.duration.missing`) | `Seclo 20 mg 1+0+1`, no days | 13 |
| Two drugs of the same class (`Rx.dup`) | Omeprazole + esomeprazole; naproxen + ibuprofen | 13 |
| Referral missing in an emergency (`Adv.refer`) | Heart attack or stroke, advice "take rest" | 5 |
| Test not fitting the sex (`Inv.relevance`) | PSA for a woman | 5 |
| Drug conflicting with history (`Rx.hs`) | Naproxen with a peptic ulcer history | 4 |
| Drug interaction (`Rx.ddi`) | Warfarin + aspirin 300 mg | 3 |
| Complaint not fitting the sex (`CC.demo`) | Vaginal discharge in a man | 3 |

## 2. Fix found during this test

The new schedule questions (01b482c) ran on the in-process model only. On vLLM there is no in-process model, so
the questions failed silently and `Ace 500 mg` got **no alert**. Added `VLLMProvider._answer_questions`, which
sends the questions to the base model on the vLLM server (adapter off, one batched request), the same way the
clinical reviewer is served. After the fix, on vLLM:

| Medicine line | Alerts | Time |
|---|---|---|
| `Ace 500 mg` (nothing after) | dose/frequency missing + duration missing | 2.3 s (local engine ~9 s) |
| `Napa 500 mg 1+1+1, 3 days` | none | 0.7 s |
| `1 tab when needed`, Bengali `১+০+১ ৫ দিন` | none | 0.7–1.2 s |
| `Seclo 20 mg 1+0+1`, no days | duration missing | 2.3 s |

Offline vLLM provider self-test: 9 of 9 pass.

## 3. Reliability and speed

| Measure | 10 doctors (rehearsal 1) | **20 doctors (this test)** |
|---|---|---|
| Section checks | 252 | **486** |
| Failures | 0 | **0** |
| Checks per minute | 56 | **93** |
| Layer 1 wait: median / 95% / slowest | 1.35 / 3.7 / 5.8 s | **2.36 / 6.4 / 9.8 s** |
| Layer 2 wait: median / 95% / slowest | 1.56 / 4.3 / 6.3 s | **2.38 / 8.0 / 14.4 s** |
| Both layers per section: median | ≈3.3 s | **5.3 s** (95%: 12.0 s) |
| Per-doctor median wait | 3.0–3.6 s | **4.4–6.4 s** |

Wait by section (both layers):

| Section | Checks | Median | 95% within |
|---|---|---|---|
| Past history | 20 | 3.4 s | 5.7 s |
| Advice | 100 | 3.9 s | 8.9 s |
| Diagnosis | 100 | 4.8 s | 8.9 s |
| Investigations | 66 | 5.6 s | 8.7 s |
| Complaints | 100 | 6.1 s | 12.5 s |
| **Medicines (Rx)** | 100 | **8.1 s** | **15.7 s** |

Every doctor completed all their checks (21–26 each). The medicine section is the slowest because it now asks one
extra question per medicine line. 191 of 486 checks took over 6 s and 56 over 10 s, all under this compressed pace;
with real doctors spreading their sections over minutes, the queue will be much shorter (the 1-doctor wait is
0.7 s for layer 1).

## 4. Errors caught

Scoring is strict: an error counts as caught only when an alert names its line **and** is the right kind of alert
(e.g. "side of the knee not stated" quoting the naproxen line does not catch "naproxen with a peptic ulcer").

| | Caught | Share |
|---|---|---|
| Layer 1 (fast check) | 55 of 88 | 63% |
| Layer 2 (clinical reviewer) | 3 of 88 | 3% |
| **Whole system** | **56 of 88** | **64%** |
| Prescriptions with every error caught | 44 of 75 | 59% |

By error type:

| Error type | Caught | Share | |
|---|---|---|---|
| Complaint with no duration | 14 / 14 | 100% | ✔ |
| Medicine with no duration | 13 / 13 | 100% | ✔ (new check) |
| Medicine with no dose/frequency | 13 / 14 | 93% | ✔ (new check; missed an inhaler with nothing written) |
| Referral missing in an emergency | 5 / 5 | 100% | ✔ |
| Test not fitting the sex | 5 / 5 | 100% | ✔ |
| Complaint not fitting the sex | 3 / 3 | 100% | ✔ |
| Antibiotic with no infection | 3 / 14 | 21% | ✘ |
| Two drugs of the same class | 0 / 13 | 0% | ✘ |
| Drug conflicting with history | 0 / 4 | 0% | ✘ |
| Drug interaction | 0 / 3 | 0% | ✘ (known weak type) |

**Missing information: 53 of 54 caught (98%). Wrong choice of medicine: 3 of 34 caught (9%).**

Why the medicine-choice errors are missed:
- **Duplicates (`Rx.dup`) and antibiotics without a reason (`Rx.over`)** are in the rule list, but the fine-tuned
  model rarely raises them; on validation `Rx.over` was 11 of 21 (`docs/weak_error_types_data_request.md`). The
  clinical reviewer did not pick them up either: its answers focus on documentation (side, severity, sex).
- **Drug conflicting with history** and **interactions** depend on the medicine facts the model reads; if the
  drug database does not list the naproxen–ulcer or warfarin–aspirin problem, the model does not flag it.
- These are the same weak types already in the data plan (decisions 3–6), now with fresh examples.

## 5. False alerts

77 alerts on lines without an error (and 17 that repeat a real error from another section, not counted as false).
406 error-free sections, of which **327 (81%) had no alert at all**.

| Alert | Layer | Count | Judgement |
|---|---|---|---|
| "Side not stated" (knee, ear, back) | 2 | 28 | Documentation reminder |
| "Severity not stated" (headache, pain, breathlessness) | 2 | 24 | Documentation reminder |
| "Required medicine missing" (`Rx.cc.sym`), e.g. heart attack "needs more", otitis media | 1 | 7 | Mostly wrong |
| Diagnosis does not explain the complaint, e.g. "headache but hypertension not in complaints" | 1 | 6 | Wrong; same habit as before |
| Serum creatinine questioned before the diagnosis is written | 1 | 6 | Order effect: investigations are checked before the diagnosis section exists |
| Meal timing missing for an inhaler (`Rx.timing`) | 1 | 3 | Wrong: does not apply to inhalers |
| Other | 1–2 | 3 | One-offs |

Two-thirds of false alerts are layer 2's side/severity reminders. If doctors find them noisy, these are the first
to tune (in the reviewer prompt, not by hand-written rules).

## 6. GPU load

| | 10 doctors | **20 doctors** |
|---|---|---|
| GPU busy, mean / peak | 84% / 100% | **94% / 100%** |
| Power, mean / peak | 266 / 344 W | **311 / 360 W** (cap 400 W) |
| Memory | 29.7 GB | 29.7 GB (reserved at start) |
| Energy | 20 Wh (0.4 Wh per prescription) | **27 Wh (0.27 Wh per prescription)** |

At this compressed pace the GPU was close to saturated; it stayed under the 400 W cap. Real use will be bursty
and lighter.

## 7. Conclusions

1. **Capacity: ready for 20 doctors.** No failures at a load far heavier than real use. The cost of more doctors is
   wait time, which grows gradually (median 3.3 s → 5.3 s from 10 to 20 doctors at this pace).
2. **The new schedule check works on vLLM after the fix** and is faster there than on the local engine.
3. **Accuracy depends on the kind of error.** Missing information: 98% caught. Medicine-choice errors: 9%. Tell the
   pilot doctors plainly that the system does **not** yet check duplicates, unneeded antibiotics, interactions or
   contraindications reliably.
4. **Next model work (data plan):** examples and knowledge for `Rx.dup`, `Rx.over`, `Rx.hs`, `Rx.ddi`; drug
   database entries for common interactions and contraindications (PULSE-Rx rules, clinician-approved); tune the
   reviewer's side/severity reminders.

## 8. Correction to rehearsal 1

With the strict scoring used here, rehearsal 1 (`docs/pilot_simulation_report.md`) caught **39 of 44 (89%)**, not
40 of 44: one PSA-for-a-woman error was matched by an alert of the wrong kind. Its other conclusions are unchanged.

## Limits

- Synthetic prescriptions from templates; the same team wrote the cases and expected answers. Real prescriptions
  are messier (real-prescription catch rate: 79%, `docs/model_register.md`).
- Some types have only 3–5 cases.
- Compressed pace makes waits longer than in real use, and makes the GPU look busier.
- Sections are checked in the app's order (complaints, history, investigations, diagnosis, medicines, advice); an
  investigation is checked before the diagnosis is written, which caused 6 of the false alerts.

Raw results: `benchmark/results/pilot_simulation_20.json`.
