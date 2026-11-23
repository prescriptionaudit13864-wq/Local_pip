# vLLM equivalence report (T01.3)

- Engine A: `equiv_local.json` — LocalChatProvider unsloth/gemma-4-12B-it-qat-q4_0-unquantized
- Engine B: `equiv_vllm.json` — VLLMProvider unsloth/gemma-4-12B-it-qat-q4_0-unquantized
- Section checks compared: 99 (missing in one file: 0)

| Layer | Same alert list | Share | Target 95% | Same wording too |
|---|---|---|---|---|
| Layer 1 (fast check) | 99 of 99 | 100.0% | pass | 93 of 99 |
| Layer 2 (clinical reviewer) | 95 of 99 | 96.0% | pass | 87 of 95 |

| Speed (median s) | A | B |
|---|---|---|
| Layer 1 | 2.05 | 0.59 |
| Layer 2 | 2.72 | 0.63 |

## Every difference

| Layer | Check | Only in A | Only in B |
|---|---|---|---|
| Layer 2 | 7:chiefComplaints CC: pregnancy in a man + headache with no duration | - | [('sex_age_anatomy', 'pregnancy for 2 months')] |
| Layer 2 | 9:investigations Inv: PSA for a woman | [('sex_age_anatomy', 'psa (prostate specific antigen)')] | - |
| Layer 2 | 10:investigations Inv: Pap smear for a man | - | [('sex_age_anatomy', 'pap smear')] |
| Layer 2 | 27:chiefComplaints Impossible duration | - | [('implausible', 'weight loss for 1 day')] |
