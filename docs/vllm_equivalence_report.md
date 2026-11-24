# vLLM equivalence report (T01.3)

- Engine A: `equiv_local.json` — LocalChatProvider unsloth/gemma-4-12B-it-qat-q4_0-unquantized
- Engine B: `equiv_vllm.json` — VLLMProvider unsloth/gemma-4-12B-it-qat-q4_0-unquantized
- Section checks compared: 100 (missing in one file: 0)

| Layer | Same alert list | Share | Target 95% | Same wording too |
|---|---|---|---|---|
| Layer 1 (fast check) | 100 of 100 | 100.0% | pass | 94 of 100 |
| Layer 2 (clinical reviewer) | 94 of 100 | 94.0% | FAIL | 87 of 94 |

| Speed (median s) | A | B |
|---|---|---|
| Layer 1 | 2.09 | 0.61 |
| Layer 2 | 2.78 | 0.64 |

## Every difference

| Layer | Check | Only in A | Only in B |
|---|---|---|---|
| Layer 2 | 7:chiefComplaints CC: pregnancy in a man + headache with no duration | - | [('sex_age_anatomy', 'pregnancy for 2 months')] |
| Layer 2 | 9:investigations Inv: PSA for a woman | [('sex_age_anatomy', 'psa (prostate specific antigen)')] | - |
| Layer 2 | 12:diagnosis Dx: male, pregnancy first | - | [('sex_age_anatomy', 'gestational diabetes mellitus'), ('sex_age_anatomy', 'pregnancy in 3rd trimester')] |
| Layer 2 | 17:chiefComplaints Male, pregnancy plus a complaint with no duration among others | - | [('side_missing', 'knee joint pain for 3 weeks')] |
| Layer 2 | 25:rx Adult paracetamol dose for a 4-year-old | [('unsafe_for_patient', 'napa 500 mg (1 + 1 + 1) - (3 day(s)) [after meal]')] | - |
| Layer 2 | 28:chiefComplaints Impossible duration | - | [('implausible', 'weight loss for 1 day')] |
