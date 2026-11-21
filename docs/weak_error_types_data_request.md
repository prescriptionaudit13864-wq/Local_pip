# Why v5 misses some error types, and the data we need (plan tasks A03.2, A04.1; 21 Nov 2026)

v5 catches 79% of errors on real prescriptions overall, but some error types almost never
(`docs/model_register.md`). Before collecting more data we read every missed validation row and the rule text
the model is given (`finetune_taxonomy_v7.json`). Most misses will not be fixed by more rows of the same kind.
No hand-written alert rules or invented examples are added: new training rows come only from physician labels
and from facts in the drug database or from approved PULSE-Rx rules.

## Error examples in the training data now

Counted once per prescription and section (`LLM_Trainer/build_weak_review.py`). Meanings are the rule names in
the rule list.

| Error type | Rule name | Training + validation rows | From real prescriptions | v5 caught on validation |
|---|---|---|---|---|
| `Inv.dx` | confirmatory investigation missing or unnecessary | 24 | 24 | 0 of 5 |
| `Rx.cc.sym` | required medication missing | 52 | 34 | 0 of 7 |
| `Rx.ddi` | drug-drug interaction unaddressed | 31 | 0 | 0 of 3 |
| `Dx.hs.1` | diagnosis contradicts clinical findings | 19 | 18 | 0 of 2 |
| `Inv.hs` | investigation not aligned with findings | 37 | 7 | 1 of 6 |
| `Rx.demo` | dose, duration or form does not fit age or weight | 35 | 17 | 2 of 6 |
| `CC.4` | site or source missing | 21 | 1 | 1 of 4 |
| `Rx.over` | medicine without a matching condition | 157 | 11 | 11 of 21 |
| `Adv.refer` | referral missing for a serious condition | 63 | 6 | 6 of 12 |
| `CC.time` | implausible duration | 208 | 0 | test: 4 of 8 |

## What we found

1. **The rule text contradicts the labels.** The model follows the rule text, so where the text and the labels
   disagree, it follows the text and misses the label.
   - `Inv.dx` text: a confirming test is required *only* when the diagnosis is marked provisional or suspected,
     and the model must *not* ask for tests to rule out other causes. The physician labels flag a settled
     "fever, unspecified" for missing CBC, NS1 or Widal. Under the text, the model is right not to flag these.
   - `Inv.hs` text is about tests that are *not justified* by findings. Most labels are about a *needed test
     missing* (no ECG for chest pain on exertion; no chest X-ray or sputum test for cough over 3 weeks with
     weight loss).
   - `Dx.hs.1` text is "diagnosis contradicts findings". The labels say "common cold is in the diagnosis
     but its symptoms are not in the complaint", which is a documentation gap, not a contradiction.
   - `Rx.demo` is also used for "diabetes medicine should be continued or followed up", which is not an age or
     weight problem.
2. **Vague reasons.** Several `Inv.dx` labels say only "Xray.", "More investigations regarding fever" or
   "CBC is suggested". Two doctors may disagree on these.
3. **Facts not shown.** In 2 of the 3 interaction misses, the medicine facts the model reads have no
   interaction for that pair (tramadol + sertraline), because our drug database does not list it. In training,
   every interaction example has its interaction line in the facts. The model learned "flag only when the facts
   show it", so the gap is in the drug database, not the model.
4. **Missing-treatment judgements.** The `Rx.cc.sym` misses are about something *not* prescribed (no scabicide
   for scabies, no antifungal for tinea, no laxative for constipation). These need knowledge of the standard
   treatment, which the facts do not give. The clinical reviewer (layer 2) is better placed for them now.
5. **No training examples for the regression.** No training complaint mentions "weight loss", and the duration
   examples come from a few templates.

## Decisions and data we need

| # | From | What | Fixes | By |
|---|---|---|---|---|
| 1 | Dr. Israt | For `Inv.dx`, `Inv.hs`, `Dx.hs.1`: which is right, the rule text or the labels? If the labels are, she rewrites the rule text in her words (the definition, not an alert rule); if the text is, those labels are moved or removed | cause 1 | W02 |
| 2 | Dr. Israt | Check the 100 priority-1 rows in `LLM_Trainer/data/weak_types_review.csv` (real prescriptions, the 5 weakest types): is the label right, which type it should be if not, and does the prescription show enough to decide? About 3 weeks at 30-40 rows a week | causes 1, 2 | W02-W04 |
| 3 | Dr. Israt | For `Rx.cc.sym`: the common diagnoses where a treatment is *required* (e.g. scabies: scabicide). This becomes knowledge the model reads, like Drug_Facts, not a rule that raises an alert | cause 4 | W03 |
| 4 | Reyana | Interaction pairs from PULSE-Rx or a referenced source, approved by a clinician, added to the drug database so that the facts show them | cause 3 | W03-W04 |
| 5 | Anik + Dr. Israt | 300-500 new real prescriptions from CMED clinics not in training. The reviewer pre-screens them on this PC; Dr. Israt labels those it marks for the weak types, plus a random share so that the test is not biased | all types; also the fresh test set | W02-W05 |
| 6 | Tanim | Rebuild the v8 data from the corrected rule text, labels and facts; train for 1 epoch or with early stopping; score with `score_ci.py`, checking `CC.time` against v4 | all | W06-W08 |

Rows 1-4 are physician or clinician decisions, and nothing enters training until it is approved. Rows built
from lessons (priority 3 in the sheet) stay as they are unless decision 1 changes a rule's meaning.

## How we will know it worked

Each weak type needs at least 20 labelled test examples before we quote a number for it. The v8 target on
validation is at least 50% caught for each of the 5 weakest types, while the share of correct sections left
without a false alert stays at 90% or above.
