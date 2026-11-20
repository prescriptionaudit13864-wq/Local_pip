# Model register

One entry per model release. A model is used in the app only after it has an entry here. Scores are on
held-out files the model never trained on, with 95% confidence intervals (see `LLM_Trainer/score_ci.py`).

## model-v5.0 (installed 2026-11-20)

| Item | Value |
|---|---|
| Base model | `unsloth/gemma-4-12B-it-qat-q4_0-unquantized` |
| Adapter | `LLM_Trainer/outputs/unsloth__gemma-4-12B-it-qat-q4_0-unquantized__mapping_v5/adapter_gold` |
| Adapter SHA-256 (first 16) | `8d9d825dc44bfa4b` (same file as `ckpt_gold/checkpoint-250`) |
| Checkpoint used | Step 250 of 746 (epoch 0.67), lowest validation loss 0.0361; the final step-746 weights were not used |
| Start point | Silver adapter `qlora4bit/adapter_silver` |
| Training recipe | Gold stage only, 2 epochs, learning rate 1e-4, LoRA r 16 / alpha 32, effective batch 16, 4-bit, max length 3,072, seed 3407 (`mapping_v5.env`) |
| Training data | `gold_v7_knowledge_train.jsonl`, 5,953 rows |
| Validation data | `gold_v7_knowledge_validation.jsonl`, 582 rows (used to pick the checkpoint, so it is not a pure test) |
| Test data (never used in training or checkpoint choice) | `knowledge_v7_test.jsonl` 148 rows; `drugfacts_v7_test.jsonl` 90 rows |
| Rule list in the app | `audit_pipeline/configs/finetune_taxonomy.json` = `finetune_taxonomy_v7.json` (SHA-256 `83e66092ed5c`) |
| App settings | `HF_QUANT=4bit`, `AUDIT_PROMPT_MODE=finetune`, `AUDIT_MODEL_ONLY=1`, `FINETUNE_UNSUPPORTED=omit`, clinical reviewer on |
| Code version | `clean-v1` at `caa2cce` |

### Fit (overfitting check)

Training loss kept falling in epoch 2 (0.029 to 0.004) while validation loss stopped improving after step 250
(0.036, then 0.037-0.040). The second epoch overfitted mildly. Training kept the best validation checkpoint
(step 250), so the installed model is from before the overfitting.

| Step | Epoch | Training loss | Validation loss |
|---|---|---|---|
| 100 | 0.27 | 0.074 | 0.051 |
| 250 | 0.67 | 0.029 | **0.036 (best)** |
| 400 | 1.07 | 0.012 | 0.037 |
| 746 | 2.00 | 0.004 | 0.038 |

Next round: 1 epoch or early stopping, since nothing was gained after step 250.

### Leakage check (`LLM_Trainer/check_leakage.py`, 2026-11-21)

| Held-out file | Same prescription in training | Exact copies | Near copies (context 90% the same) |
|---|---|---|---|
| `knowledge_v7_test` (148) | 0 | 0 | 0 |
| `drugfacts_v7_test` (90) | 0 | 0 | 16: same pregnancy template, different medicine (0 of 90 medicine lines seen in training) |
| `gold_v7_knowledge_validation` (582) | 0 | 25 (Demo rows with only age and sex, no error) | 44 (mostly Rx and advice rows) |

The two test files are clean. Validation results are reported with and without the 69 copied rows.

### Held-out scores

Layer 1 alone, 95% Wilson CIs; full per-rule tables in `LLM_Trainer/data/score_ci_mapping_v5_vs_mapping_v4.md`.

| File | Errors caught | Correct rows left clean | Whole answer right |
|---|---|---|---|
| `knowledge_v7_test` (148 rows, 45 different entries) | 85/92 = 92% (85-96) | 55/56 = 98% (91-100) | 41/45 entries = 91% (79-96) |
| `drugfacts_v7_test` (90, unseen medicines) | 79/86 = 92% (84-96) | 20/23 = 87% (68-95) | 78/90 = 87% (78-92) |
| `gold_v7_knowledge_validation` without 64 copied rows (518) | 261/332 = 79% (74-83) | 217/233 = 93% (89-96) | 83% (80-86) |

Compared with v4 (complaint and history test, same labels): v4 caught 87/92; v5 caught 85/92 and lost
`CC.time` ("Weight loss for 1 day" missed, 4/8 vs 8/8). v4's drug-test and validation results were scored on
older versions of those files and cannot be compared.

Not yet caught on real prescriptions: `Inv.dx` 0/5, `Rx.cc.sym` 0/7, `Rx.ddi` 0/3, `Dx.hs.1` 0/2, `Inv.hs` 1/6,
`Rx.demo` 2/6, `CC.4` 1/4; weak: `Rx.over` 11/21, `Adv.refer` 6/12.
