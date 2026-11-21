# How accurate is the PrescribeX model? (evaluation note, 21 Nov 2026)

## 1. What the earlier "85%" was

It came from the 15 cases our doctor reported. Those cases were picked to show known problems, and the fixes
were checked on the same cases. 14 of 15 flagged has a 95% confidence interval of about 70% to 99%. It shows the
fixes work; it is not an accuracy figure, and we should not quote it as one.

## 2. How we test now

The installed model (v5, see `docs/model_register.md`) is scored on two test files that were never used for
training and never used to choose the model:

- **Complaint and history test:** 148 rows, but only 45 different entries (the same entry repeated with other
  ages and sexes). Covers wrong sex or age for a complaint, impossible durations, duplicated history, missing detail.
- **Drug test:** 90 prescriptions with medicines the model never saw in training (checked: 0 of 90 medicine lines
  appear in the training data). Covers pregnancy risk, contraindication, duplicate drugs, timing.

A leakage check found no copies of either test file in the training data.

## 3. Results (layer 1, the fine-tuned fast check alone)

| Test | Errors caught (95% CI) | Correct rows left without a false alert | Whole answer right |
|---|---|---|---|
| Complaint and history | 85 of 92 = **92% (85-96%)** | 55 of 56 = 98% (91-100%) | 41 of 45 different entries = 91% (79-96%) |
| Drugs never seen in training | 79 of 86 = **92% (84-96%)** | 20 of 23 = 87% (68-95%) | 78 of 90 = 87% (78-92%) |

The harder check is the **validation file**: 582 sections of real prescriptions labelled by physicians (plus
lessons), in all sections. It was used to choose the checkpoint, so it is slightly optimistic; 64 rows similar to
training rows were removed for this line.

| Validation (real prescriptions, physician labels) | Errors caught (95% CI) | Correct sections left without a false alert | Whole answer right |
|---|---|---|---|
| 518 sections, all sections | 261 of 332 = **79% (74-83%)** | 217 of 233 = 93% (89-96%) | 83% (80-86%) |

How to say it: "On real prescriptions labelled by physicians, the model raised 79% (95% CI 74-83%) of the errors
and left 93% of correct sections without a false alert. On held-out tests of medicines it had never seen, it
raised 92% (84-96%)."

## 4. Weak points we found

- **Impossible complaint durations:** "Weight loss for 1 day" was missed every time (4 of 8 duration errors
  caught; the previous model caught 8 of 8). In the app the clinical reviewer (layer 2) still flags this case,
  but the fast check regressed. It goes into the next training round.
- **Pregnancy at age 70:** flagged, but under the wrong error type (duration instead of age).
- **Medicine timing:** 40 of 46 caught (87%, 74-94%).
- **Error types the model does not yet catch on real prescriptions:** investigation not matching the diagnosis
  (0 of 5), required medicine missing (0 of 7), drug interactions (0 of 3), diagnosis contradicting the findings
  (0 of 2), investigation not matching the findings (1 of 6), medicine without a matching condition (11 of 21, 52%),
  referral advice (6 of 12). For several of these the rule text the model reads disagrees with the physician
  labels; see `docs/weak_error_types_data_request.md` (plan tasks A03.2, A04.1).
- **Rare error types:** some have only 4-8 test examples, so their intervals are very wide (e.g. 4 of 4 = 51-100%).
  We do not claim numbers for them until there are at least 20 examples each.

## 5. Is it overfitting?

Training loss kept falling in the second epoch (0.029 to 0.004) while validation loss stopped improving at step
250 of 746 (0.036). That is mild overfitting in epoch 2. Training kept the best-validation checkpoint (step 250),
and the installed adapter is that checkpoint (same file hash), so the model in use is from before the overfitting.
Next round: one epoch or early stopping. The main remaining weakness is the opposite problem: rare error types
have too few examples (underfitting).

## 6. Limits of these numbers

- The test files were built by the team from physician-reviewed rules and lessons. They measure how well the
  model catches the error types it was taught, not how often errors occur in real practice.
- Real-world accuracy will come from three steps already in the plan: a fresh test set of real prescriptions
  from clinics and months not in training (W02-W05), doctors held out of the pre-pilot (W09), and the pilot sample
  labelled by Dr. Israt, which is the number to quote in papers (W27).
- These are layer-1 scores. The app also runs the clinical reviewer (layer 2), which is measured separately in
  the live tests.

Sources: `LLM_Trainer/data/score_ci_mapping_v5_vs_mapping_v4.md`, `LLM_Trainer/data/leakage_report.json`,
training log `mapping_v5/ckpt_gold/checkpoint-746/trainer_state.json`.
