# Engine audit: current local engine vs vLLM (24 Nov 2026)

Test: 10 new synthetic prescriptions with a known answer (`benchmark/engine_audit.py`): 7 hold 8 real errors, 3 are
clean. Each was written section by section, the way the app does: 26 section checks, each with layer 1 (the
fine-tuned fast check) and then layer 2 (the clinical reviewer). The GPU was sampled every 0.5 s. Same PC, same
model, same adapter, same audit code; only the engine changed. No patient data. Raw results:
`benchmark/results/engine_audit_local.json`, `benchmark/results/engine_audit_vllm.json`.

## What the two engines are

| | Current local engine (`AUDIT_PROVIDER=local`) | vLLM (`AUDIT_PROVIDER=vllm`) |
|---|---|---|
| What it is | Hugging Face transformers inside the audit server (Windows), one request at a time behind a GPU lock | A dedicated model server (WSL2 Ubuntu, vLLM 0.34) that the audit server calls over HTTP |
| Weights | Gemma 4 12B in **4-bit** (bitsandbytes) + v5 LoRA | Gemma 4 12B in **bf16 (16-bit)** + v5 LoRA |
| Layer 1 / layer 2 | One model; the adapter is switched on for layer 1 and off for layer 2 | Base model loaded once; requests name `prescribex` (LoRA on) or the base name (LoRA off) |
| Many requests | Queued: each waits for the one before | Continuous batching + paged KV cache: many requests share each GPU step |
| GPU memory | ~11.5 GB | ~29.5 GB (vLLM reserves 95% of the card up front) |

## Results

### Accuracy (8 real errors, 3 clean cases)

| | Local: caught | Local: false alerts | vLLM: caught | vLLM: false alerts |
|---|---|---|---|---|
| Layer 1 (fast check) | 7 of 8 | 2 | **8 of 8** | 2 |
| Layer 2 (clinical reviewer) | 3 of 8 | 1 | **4 of 8** | 2 |
| Whole system (either layer) | **8 of 8** | **3** | **8 of 8** | 4 |
| Cases fully right (all errors, no false alert) | 7 of 10 | | 6 of 10 | |

Per case (layer 1 | layer 2 | whole system):

| Case | Local | vLLM |
|---|---|---|
| S01 Male with a menstrual complaint | 1/1 \| 0/1 \| 1/1 | 1/1 \| **1/1** \| 1/1 |
| S02 Complaint with no duration | 1/1 \| 0/1 \| 1/1 | 1/1 \| 0/1 \| 1/1 |
| S03 Illness longer than life | 1/1 \| 0/1 \| 1/1 | 1/1 \| 0/1 \| 1/1 |
| S04 Prostate test for a woman | **0/1** \| 1/1 \| 1/1 | 1/1 \| 1/1 \| 1/1 |
| S05 Pregnancy diagnosis in a man | 1/1 \| 0/1 + 1 false \| 1/1 + 1 false | same |
| S06 Adult dose for a 3-year-old | 1/1 \| 1/1 \| 1/1 | same |
| S07 Clean hypertension visit | 1 false (L1) | 1 false (L1) + **1 false (L2)** |
| S08 Clean child diarrhoea visit | 1 false (L1) | 1 false (L1) |
| S09 Clean adult cough visit | clean | clean |
| S10 Two errors in one visit | 2/2 \| 1/2 \| 2/2 | 2/2 \| 1/2 \| 2/2 |

The false alerts are the same model habits on both engines: "hypertension not in the complaints" for a headache visit
(layer 1), "hygiene advice missing" for diarrhoea (layer 1), "severity not stated" for abdominal pain (layer 2).
vLLM adds one more "severity not stated" (headache, layer 2).

### Time

| | Local | vLLM |
|---|---|---|
| All 10 cases (26 checks, both layers) | 202 s | **55 s** (3.7× faster) |
| Layer 1 per check: median / slowest | 2.40 s / 12.5 s | **0.73 s / 3.3 s** |
| Layer 2 per check: median / slowest | 1.76 s / 10.2 s | **0.66 s / 3.5 s** |

### Several doctors at once (`benchmark/vllm_load_test.py`)

| Doctors at once | Local: checks/min | Local: layer-1 median wait | vLLM: checks/min | vLLM: layer-1 median wait |
|---|---|---|---|---|
| 1 | 6.7 | 2.0 s | 25 | 0.7 s |
| 4 | 7.7 | **12.8 s** | 63 | 1.0 s |
| 8 | – | – | 98 | 1.3 s |
| 16 | – | – | 116 | 2.8 s |

Local: 6 checks per doctor (23 Nov run with 24 was too slow to repeat). vLLM: `docs/vllm_load_report.md`. 0 errors on both.

### GPU load during the 10 cases (400 W cap)

| | Local | vLLM |
|---|---|---|
| GPU busy (mean / max) | 43% / 98% | 94% / 100% |
| Power (mean / max) | 118 W / 229 W | 274 W / 304 W |
| Memory used (max) | 11.5 GB | 29.5 GB |
| Energy for the 10 cases (mean power × time) | ~6.6 Wh | **~4.2 Wh** |

vLLM keeps the GPU busier and draws more power while working, but it finishes much sooner, so it uses less energy
for the same work. Both stayed well under the 400 W cap. vLLM's memory is reserved at start, not a sign of load:
idle it still shows ~29.5 GB.

## How they differed, and why

1. **Accuracy is the same at system level** (8 of 8 on both). Layer 1 on vLLM caught the PSA test for a woman,
   which the 4-bit local layer 1 missed (local layer 2 caught it instead). Layer 2 on vLLM flagged one more real
   error (S01) and one more false alert (S07).
2. **The differences come from the weights, not the engine.** vLLM runs the full 16-bit model; the local engine
   runs a 4-bit copy, which changes a few borderline answers. Equivalence on the 100 team test cases
   (`docs/vllm_equivalence_report.md`): layer 1 the same alerts in 100 of 100 checks; layer 2 in 94 of 100, where
   vLLM caught 4 errors that the local engine missed and missed 2 that it caught.
3. **Speed and many users are where they really differ.** One doctor waits 3–4× less on vLLM. With 4 doctors the
   local engine queues them (12.8 s median wait for the fast check) while vLLM batches them (1.0 s).

## Which suits best

- **For the pilot with many doctors: vLLM.** Same accuracy, 3–4× faster for one doctor, about 8× more checks per
  minute with several doctors, and less energy per check. This is what the supervisor asked for.
- **Keep the local engine as the fallback and for development** (`AUDIT_PROVIDER=local`): no WSL needed, a third of
  the GPU memory (other GPU jobs can run beside it), fine for one person testing.

Things to watch with vLLM:
- It takes the whole GPU (29.5 GB). Training or other GPU jobs cannot run next to it: stop it first.
- It runs in WSL2 and stops when its terminal closes. For the pilot, start it from a terminal that stays open, or
  as a startup task.
- Layer-2 agreement is 94%, just under the 95% target; the differences go both ways (see point 2). A larger
  gold-set test should decide whether bf16 or 4-bit layer 2 is more accurate.

## Limits of this test

10 synthetic cases is small: one case changes a share by 10 points. Same team wrote the cases and the expected
answers. Ages: both engines described the 3-year-old as 4 and the 19-year-old as 20 (from a 1 January birth date),
so age wording should be checked in the app, but it did not change any alert here.
