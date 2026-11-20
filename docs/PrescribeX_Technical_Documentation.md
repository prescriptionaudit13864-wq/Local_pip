# PrescribeX — Technical Documentation

**Purpose of this document.** A complete, self-contained description of the PrescribeX project: the clinical problem,
the data, the knowledge base, the audit pipeline, the fine-tuned language model, the training process, the evaluation,
and the current state. It is written so that another AI assistant (or an engineer new to the project) can understand
the system and continue work without access to earlier conversations.

**State as of:** 2026-11-15.
**Conventions used here:**
- "Section codes": `Demo`, `CC`, `Hx`, `Inv`, `Dx`, `Rx`, `Adv` (defined in §3.2).
- "Rule ID": an identifier such as `CC.3` or `Rx.timing` (see §5).
- "Layer 1": the fine-tuned fast check. "Layer 2": the clinical reviewer. (§6, §7)
- Paths are relative to the folder named at the start of each section. All software runs on one Windows 11 PC.
- Numbers come from measured runs; where a figure is an estimate, it says so.

---

## 1. Project summary

### 1.1 What PrescribeX is
PrescribeX is a prescription-writing application with a **live clinical audit**. A doctor in Bangladesh primary care
writes a prescription section by section (complaints, past history, investigations, diagnosis, medicines, advice).
After each section is saved, the system checks that section — in the context of everything written so far — for
clinical and documentation errors, and shows alerts with a short case-specific explanation. A final audit runs on
the complete prescription when it is saved.

### 1.2 Goals and constraints (decided by the project owner)
1. **The model decides.** No hand-written code rule may raise an alert by itself. Hand-written rules may only
   *propose* what to check; a language model makes the decision. (Implemented: `AUDIT_MODEL_ONLY=1`,
   `FINETUNE_UNSUPPORTED=omit`, §10.)
2. **Fully local.** Inference runs on the local GPU. Patient data does not leave the machine. A cloud model was tested
   (§9.6) but is not used.
3. **Fast.** Target: each section's alert within about 5–6 seconds.
4. **No alert fatigue.** Prefer fewer, correct, specific alerts over many uncertain ones.
5. **Bangladesh context.** Bangla and English text, local brands (medex drug data), Bangladesh guidelines (IMCI 2019,
   national guidelines), local practice.
6. **Specific explanations.** Each alert must name the entry and what is wrong ("'Earache for 3 days': the side of
   the ear is not specified"), not a generic rule text.

### 1.3 Hardware
- GPU: NVIDIA RTX 5090, 32 GB. Power capped at 400 W and SM clock locked at about 2,100 MHz by a startup scheduled
  task ("GPU power cap"), because the PC lost power several times. The power losses also occurred at idle, so the
  cause is believed to be the PSU or wall power, not GPU load (see §11.4).
- OS: Windows 11. Windows Smart App Control was found to block compiled Python extensions (`.pyd`) and Triton caches;
  it must be off for training.

---

## 2. Repository and folder map

| Folder | Role |
|---|---|
| `C:\Users\HEAT\Documents\Local Pipeline\Local_pip` | **Main project**: the React app, the audit pipeline (Python package `audit_pipeline`), benchmarks, run scripts. Git repository (branch `real-prescription-eval`). |
| `C:\Users\HEAT\Documents\LLM_Trainer` | **Training**: data building scripts, training data (`data/`), training configs (`*.env`), `train.py`, evaluation scripts, trained outputs (`outputs/`), physician review pages (`review_pages/`). |
| `C:\Users\HEAT\Documents\V_Pipe\Vpip` | Earlier generation of the pipeline (v3), guideline extraction scripts, and the physician-reviewed prescription file `v3/data/prescription_XL.csv` (contains patient names — private). |

### 2.1 Main project (`Local_pip`)
```
Local_pip/
  audit_pipeline/            Python package: the audit pipeline and the app server (§6)
    configs/                 protocol.yaml (rulebook), finetune_taxonomy.json (layer-1 rule list),
                             medicine_aliases.yaml, frequency_cache.json
    data/                    medicine.csv, generic.csv (drug catalogue), icd11_mms_2026_01.sqlite3,
                             investigation catalogues, rag/ (knowledge base files, §4)
  prescribex-app/            React 19 + Vite 8 + Tailwind 3 front end; backend/data/ = saved visits (JSON per step)
  benchmark/                 test and measurement scripts, results/ (§9)
  docs/                      this document
  run_local.ps1              starts the audit server and the app (reads .env fresh)
  install_model.ps1          installs a trained adapter + matching rule list (with -Undo)
  .env                       configuration (§10)
  audit_server.log / .err.log  server logs written by run_local.ps1
```

### 2.2 Audit pipeline modules (`audit_pipeline/`)
| Module | Lines | Role |
|---|---|---|
| `app_server.py` | 438 | HTTP bridge on `127.0.0.1:8765`: `/health`, `/api/audit` (final), `/api/audit/section` (layer 1), `/api/audit/review` (layer 2), ICD-11 and investigation search. |
| `app_adapter.py` | 472 | Converts the app's visit JSON into the pipeline's prescription row (field names, age text, medicine lines). |
| `orchestrator.py` | 912 | End-to-end orchestration of one audit (§6.2). |
| `patient_context.py` | 486 | Structured patient context (sex, age in years, pregnancy evidence, risks). No decisions. |
| `clinical_finding_interpreter.py` | 349 | Converts fields into structured clinical evidence. |
| `medicine_mapper.py` | 947 | Parses medicine lines; maps brand → generic, strength, dosage form; daily dose. |
| `frequency_parser.py` | 311 | Parses Bangladesh frequency strings (`১ + ০ + ১`, `1+0+1`, "১ চামচ x ৩ বার"). |
| `medicine_facts.py` | 318 | Retrieves drug facts by generic ID. |
| `drug_facts.py` | 223 | Builds the `Drug_Facts` text the model reads (§4.2). |
| `guideline_rag.py`, `guideline_rag_router.py`, `rag_query_planner.py` | — | Guideline retrieval (§4.3). |
| `safety_candidate_engine.py` | 1582 | Proposes medicine-safety candidates (dose, interaction, contraindication, pregnancy…). |
| `rule_candidate_engine.py` | 2083 | Proposes section candidates (duration missing, demographic mismatch, …) and per-rule review prompts. |
| `followup_context_engine.py` | 734 | Prior-visit context and follow-up candidates. |
| `audit_task_builder.py` | 581 | Builds compact per-section audit tasks (fields + candidates). |
| `audit_writer.py` | 1112 | Calls the model provider per section, normalizes findings, consolidates overlaps, sets display level. |
| `provider.py` | 892 | Model providers. `LocalChatProvider` loads the model in-process and implements layer 1 (`audit`) and layer 2 (`review`). |
| `finetune_prompt.py` | 375 | Builds the exact training-format prompt for layer 1; maps model answers to findings; `RULE_ALIASES`. |
| `clinical_reviewer.py` | 256 | Layer 2 prompts, answer parsing, type gating (§7.5). |
| `incremental_audit.py` | 352 | Live-audit configuration and the compact response (`live_audit`). |
| `final_report.py` | 516 | Final audit report assembly. |
| `rule_text.py` | 50 | Plain-language reason and suggested action per rule (fallback text). |
| `text_translation.py`, `prescription_language.py` | — | Bangla→English hints and language normalization before parsing. |
| `icd11_terminology.py`, `investigation_catalog.py` | — | Diagnosis and test search for the app. |

### 2.3 Training folder (`LLM_Trainer`)
```
LLM_Trainer/
  train.py                   universal trainer (QLoRA via Unsloth; stages silver -> gold; --resume, --merge_only)
  *.env                      run configs: mapping_v4.env, mapping_v5.env, ...
  data/                      training/validation/test JSONL, taxonomies (finetune_taxonomy_v*.json),
                             Gold_Mapping_Review.csv (physician review export)
  build_knowledge_data.py    knowledge examples (sex/age/time/duplicate/detail)           -> gold_v3_*
  add_physician_mappings.py  physician mapping-review findings                            -> gold_v4_*
  build_v5_data.py           corrected rule descriptions, impossible-timing rows           -> gold_v5_*
  build_v6_drugfacts_data.py drug-fact examples, Rx.risk.pregnancy, Rx.contra              -> gold_v6_*
  build_v7_explanations.py   "why" sentences + all later fixes and lessons                 -> gold_v7_*
  lessons_v7.py              lesson scenarios for rare rules (AI-written, reviewed)
  imci_lessons.py            under-5 IMCI lessons
  eval_knowledge_test.py     scorer (--data=<file>)
  after_training_check.ps1   scores a trained model on the three v7 files
  review_pages/              physician review pages and their answers
  outputs/<base>__<RUN_TAG>/ adapter_gold/, ckpt_gold/checkpoint-*/, merged/
```

---

## 3. Data model

### 3.1 The prescription row (pipeline input)
`app_adapter.visit_to_row(patient, record, when)` produces a flat dictionary of strings. Keys:

| Key | Content | Notes |
|---|---|---|
| `Prescription_ID`, `Patient_ID`, `Visit_Date` | identifiers | never shown to the model |
| `Age` | `"49"` (years); `"8 months"` under 2 years; `"12 days"` under 1 month | the app's age format (`age_text`) |
| `Gender` | `"M"` / `"F"` | |
| `Chief_Complaint` | one entry **per line** (`\n`) | a line break separates entries the doctor added separately; commas inside one line may share a duration ("polyuria, thirst for 3 days") |
| `Past_History` | one entry per line | |
| `Health_Screening` | vitals, e.g. `Temperature: 97 F, Pulse Rate: 100 bpm`, BMI, MUAC | comma-separated |
| `On_Examination`, `Systemic_Examination` | e.g. `ANEMIA. (Absent)`, `ABDOMEN (NAD)` | |
| `Investigation` | ordered tests, comma-separated | |
| `Investigation_Results` | `CBC [Haemoglobin (Hb) - (9.5 g/dL), ...]` | |
| `Diagnosis` | comma-separated, often with ICD code: `M54.5 Low back pain` | |
| `Medication` | comma-separated medicine lines (format below) | |
| `Advice` | comma-separated (mostly Bangla) | |
| `ANC_Notes` | antenatal/postnatal notes | |

**Medicine line format** (`app_adapter.medication_line`):
`<Brand> <strength> (<frequency>) - (<duration>) [<instruction>]`, e.g.
`Napa 500 mg (1 + 0 + 1) - (5 day(s)) [খাবারের পরে]`. The bracketed instruction carries meal timing
("খাবারের পরে" = after meal, "খালি পেটে" = empty stomach). Frequencies may be Bangla digits (`১ + ০ + ১`).

### 3.2 Sections
| Code | Name | App section key | Fields the layer-1 model sees (`context_keys`) |
|---|---|---|---|
| `Demo` | Demographics | — | Age, Gender |
| `CC` | Chief complaint | `chiefComplaints` | Age, Gender, Chief_Complaint, Health_Screening, On_Examination, Systemic_Examination |
| `Hx` | Past history | `pastHistory` | Age, Gender, Past_History, Chief_Complaint, Health_Screening, On_Examination, Systemic_Examination |
| `Inv` | Investigation | `investigations` | Age, Gender, Chief_Complaint, Health_Screening, On_Examination, Systemic_Examination, Investigation, Investigation_Results, Diagnosis |
| `Dx` | Diagnosis | `diagnosis` | same as Inv |
| `Rx` | Medication | `rx` | Age, Gender, Chief_Complaint, Past_History, Health_Screening, On_Examination, Systemic_Examination, Investigation, Investigation_Results, Diagnosis, Medication, **Drug_Facts**, Advice |
| `Adv` | Advice | `advice` | Age, Gender, Chief_Complaint, Diagnosis, Medication, Advice, ANC_Notes |

**Writing order** in the app: complaints → past history → investigations → diagnosis → medicines → advice. A live
check only receives the sections written so far (`incremental_audit.scope_live_record`); later sections are empty.
It was verified that this partial context does not change layer-1 answers (§9.5).

### 3.3 Data sources
| Source | Size | Labels | Location |
|---|---|---|---|
| **Silver set** | 13,939 real clinic prescriptions (83,634 section rows; IDs like `SILVER_KARAIL_05015`, `SILVER_MIRPUR_04672`) | Labelled automatically by GPT using a RAG pipeline that retrieved the physician-reviewed prescriptions as examples. 36,006 rows carry an error. Not physician-checked. No names/IDs in the clinical fields. | `LLM_Trainer/data/silver_best_section_*.jsonl` |
| **Physician pool ("gold")** | 200 prescriptions (`P0001`–`P0220`) | Labelled by the project's physicians (multi-physician review + mapping review), with correction notes. Contains patient names. | `V_Pipe/Vpip/v3/data/prescription_XL.csv`; review export `LLM_Trainer/data/Gold_Mapping_Review.csv` |
| **Unlabelled pool** | ~700,000 real prescriptions | none | location to be provided by the owner (plan in §12.2) |
| App test visits | ~38 patients | none | `prescribex-app/backend/data/` |

---

## 4. Knowledge base (retrieval / RAG)

All files are local (`audit_pipeline/data/`).

### 4.1 Drug catalogue and mapping
- `medicine.csv` (brand, dosage form, generic, strength) and `generic.csv` — Bangladesh drug catalogue.
- `medicine_mapper.MedicineMapper` maps each medicine line to brand, generic, strength, dosage form, daily dose,
  duration. It chooses between a brand's variants by strength, form and a solid-form prior; combination strengths are
  matched in any order (fix: "Neobion 200 mcg / 200 mg / 100 mg" was mapped to the injection, now the tablet).

### 4.2 Drug facts (`data/rag/drug_extracted_facts_merged.jsonl`)
- 43,066 facts about 1,711 generics, extracted from medex (indications, doses by age group and route,
  contraindications, pregnancy/lactation category, major interactions).
- `physician_drug_overrides.json`: the physician's decisions for 39 pregnancy categories (whole pregnancy / first
  trimester / from 20 weeks / third trimester / not a concern / limited evidence), 5 contraindication notes and 3
  interactions judged not major. Physician wording replaces the database line.
- `drug_facts.drug_facts_text()` builds the **`Drug_Facts`** block the layer-1 model reads in the `Rx` section. No
  keyword selection: per medicine it shows a header, "used for", dose lines for the patient's age group/route,
  pregnancy and breastfeeding lines (women 12–50), up to 4 contraindications and 4 major interactions, with a fair
  share of characters per medicine (max 3,600). Example header line:
  `Ciprocin = Ciprofloxacin [Anti-diarrhoeal Antimicrobial drugs]; prescribed 1000 mg/day for 5 day(s)`.

### 4.3 Guideline facts (`data/rag/guideline_facts_merged.jsonl`)
- 63,623 facts, of which ~43,065 duplicate the drug database; ~20,500 real guideline facts from 17 sources,
  including 14 Bangladesh guidelines (4,614 facts: IMCI, SAM, IYCF, EmONC, AWaRe antibiotics, infectious-disease
  prescribing, ART, …) and Davidson's (11,578). Extraction scripts are in `V_Pipe/Vpip/v3/extract_*_facts.py`.
- **Known weakness:** guideline retrieval ignores patient context and does not prefer Bangladesh sources, and guideline
  facts are **not** currently given to the layer-1 model. (Planned work, §12.)

### 4.4 Other
- ICD-11 MMS terminology (SQLite) and investigation catalogues (Ibn Sina, Bangladesh) for the app's search boxes.

---

## 5. Rulebook

### 5.1 Three levels of rules
1. **Protocol** (`configs/protocol.yaml`): 53 *concerns* across 7 sections, each with a natural-language description
   and an example. This is the clinical rulebook.
2. **Engine check IDs**: the rule engines can raise about 90 distinct candidate IDs (protocol concerns plus
   engine-specific ones such as `Rx.dose.high`, `Rx.dup.class`, `Inv.relevance`, `Rx.risk.renal`). Engine-specific IDs
   map to trained rules via `finetune_prompt.RULE_ALIASES` (e.g. `CC.context.demo → CC.demo`,
   `Inv.relevance → Inv.cc / Inv.hs`, `Rx.dose.weight → Rx.demo`).
3. **Layer-1 taxonomy** (`configs/finetune_taxonomy.json`, currently the v7 file): 42 rules the fine-tuned model can
   output, with an instruction, the context keys, and `allowed_error_types` (rule_id, rule_name, concern) per section.

### 5.2 Layer-1 rules (42)
| Section | Rule ID → name |
|---|---|
| Demo | `Demo.1` age_gender_invalid_or_inconsistent · `Demo.2` weight_missing_when_needed_for_dosing |
| CC | `CC.1` unrecognizable_or_empty_complaint · `CC.2` severity_missing · `CC.3` duration_missing · `CC.4` site_or_source_missing · `CC.demo` chief_complaint_demographic_mismatch · `CC.hs` clinical_finding_complaint_mismatch · `CC.time` implausible_duration |
| Hx | `Hx.context.demo` history_demographic_mismatch · `Hx.time.plausibility` history_implausible_timing · `Hx.duplicate` history_duplicate_entry · `Hx.detail` history_safety_detail_missing |
| Inv | `Inv.cc` investigation_not_aligned_with_diagnosis_workup · `Inv.demo.1` age_gender_specific_investigation_missing · `Inv.demo.2` region_specific_investigation_missing · `Inv.dx` confirmatory_investigation_missing_or_unnecessary · `Inv.hs` investigation_not_aligned_with_findings |
| Dx | `Dx.cc.1` diagnosis_does_not_explain_complaint · `Dx.cc.2` diagnosis_contradicted_by_complaint · `Dx.code` icd_code_wrong · `Dx.demo` diagnosis_demographic_mismatch · `Dx.hs.1` diagnosis_contradicts_clinical_findings · `Dx.hs.2` diagnosis_omits_significant_clinical_finding · `Dx.inv.1` diagnosis_misinterprets_investigation_result |
| Rx | `Rx.cc.mis` medicine_contradicts_diagnosis_or_complaint · `Rx.cc.sym` required_medication_missing · `Rx.ddi` drug_drug_interaction_unaddressed · `Rx.demo` dose_duration_or_form_age_weight_mismatch · `Rx.dup` duplicate_or_same_class_polypharmacy · `Rx.hs` treatment_missing_for_significant_finding · `Rx.instr` medicine_route_or_instruction_incomplete · `Rx.multi` one_of_multiple_diagnoses_not_treated · `Rx.over` overprescribing_without_matching_condition · `Rx.region` treatment_not_following_bangladesh_guideline · `Rx.timing` medicine_timing_missing · `Rx.risk.pregnancy` medicine_unsafe_in_pregnancy_or_lactation · `Rx.contra` medicine_contraindicated_by_patient_condition |
| Adv | `Adv.cc` diagnosis_specific_counseling_missing · `Adv.hs` followup_advice_missing_for_significant_finding · `Adv.precau` high_risk_precaution_missing · `Adv.refer` referral_missing_for_serious_condition |

Some of these have few or no training examples (e.g. `CC.1`, `CC.hs`, `Dx.code`, `Dx.hs.2`, `Rx.hs`, `Rx.multi`,
`Rx.region`, `Inv.demo.2`, `Adv.hs`, `Adv.precau`); the model rarely or never raises them. Protocol concerns
**not** in the taxonomy (e.g. `CC.term.unclear`, `Hx.context.conflict`, `Adv.followup`) are not judged by layer 1.

### 5.3 Meaning of key rules (as taught)
- `CC.3`: a clinical symptom without duration. Exception: administrative visits (report collection, follow-up,
  pregnancy-only registration).
- `CC.demo` / `Hx.context.demo` / `Dx.demo`: impossible for the patient's sex or age (pregnancy in a male,
  hysterectomy in a male, pregnancy at 70).
- `CC.time` / `Hx.time.plausibility`: impossible duration ("weight loss for 1 day", "diabetes for 30 years" at 25).
- `Hx.detail`: vague allergy or operation ("Allergic", "Surgical History") without allergen/reaction or procedure.
- `Rx.timing`: an oral medicine without meal timing. Label rule used in training data: alert exactly when an oral
  medicine has no meal-timing note.
- `Rx.cc.mis` (revised after review): a prescribed medicine that **conflicts** with the condition or is the wrong
  treatment for it (propranolol in asthma, NSAID in bleeding ulcer, fluconazole alone for bacterial cystitis,
  carbimazole in hypothyroidism). Irrelevant medicines are `Rx.over`; missing treatment is `Rx.cc.sym`.

---

## 6. Runtime pipeline

### 6.1 Overview
```
Doctor saves a section in the React app
  │
  ├─► POST /api/audit/section   ── layer 1 (fine-tuned fast check)  ── ~1.8 s median
  │        returns live_audit {status, summary, findings[], possible_errors[], warnings[], duration_ms}
  │
  └─► POST /api/audit/review    ── layer 2 (clinical reviewer, same weights, adapter off) ── ~2.2 s median
           payload = section payload + "layer1": [why texts of layer-1 findings]
           returns review {status, problems[], hidden[], duration_ms}
Final save ─► POST /api/audit   ── full audit of all sections (final_report)
```
The frontend (`prescribex-app/src/App.jsx`) calls layer 1, shows its result, then calls layer 2 in the background
and adds a "Clinical review" block to the same section badge. The badge colour is raised by review problems:
any critical → red ("Major concern"); otherwise any review problem when layer 1 was clear → amber ("Needs review").

### 6.2 Layer 1 path, step by step
1. `app_server.AuditService.audit_section(payload)`
   - `normalize_payload` → `scope_live_record(section, record)` (only sections written so far) → `visit_to_row`.
2. `orchestrator.audit_prescription(row, options)` with `target_sections` = the section's domains and
   `live_section_audit=True`:
   - patient context (age in years, sex, pregnancy evidence), clinical evidence, medicine mapping, medicine facts;
   - `safety_candidate_engine.generate_safety_candidates` and `rule_candidate_engine.generate_rule_candidates`
     propose **candidates** (each: rule_id, evidence, why_candidate, importance, evidence_strength). Many candidates
     are generic per-rule review prompts ("Review whether …"), added so every trained rule is considered;
   - guideline RAG seeds (deterministic in live mode);
   - `audit_task_builder` makes one compact task per section (fields + candidates);
   - `audit_writer.write_audit_sections` calls the provider once per section.
3. `provider.LocalChatProvider.audit()` (takes the GPU lock) → `_audit_finetune()`:
   - `finetune_prompt.build_finetune_prompt(section, fields)` builds the **exact training-format prompt** (§7.2):
     context keys in taxonomy order, empty fields as `""` (dropped for CC/Hx), `Drug_Facts` added for Rx;
   - for `CC` and `Hx`, one extra **per-line prompt** per entry (Age, Gender and that line only) is batched with the
     section prompt, so findings are pinned to the right line and lines not flagged on their own are dropped;
   - decoding uses the **fast-concern path**: generation stops at `"concern": "`, the known concern text from the
     taxonomy is inserted (up to the space before its last word), and the model continues; rounds are capped at
     140 tokens; any doubt falls back to normal decoding (verified identical on 40/40 cases);
   - the answer's detected rules are matched to the candidates (`findings_from_detected`); with
     `FINETUNE_MODEL_FIRST=1` a rule the model flags with no matching candidate still becomes a finding
     ("model-first"); rules not in the taxonomy are dropped (`FINETUNE_UNSUPPORTED=omit`);
   - each finding's text is the model's own "why" (the line's own answer for CC/Hx).
4. `audit_writer` normalizes findings, consolidates known double-firing rule pairs
   (`_OVERLAPPING_RULE_GROUPS`, e.g. `{Rx.contra, Rx.cc.mis}`), and sets the display level
   (`_concern_level`: critical / major / moderate / minor; sex/age impossibilities show as major).
5. `incremental_audit.summarize_live_report` builds `live_audit`. With `AUDIT_MODEL_ONLY=1` it does **not** add the
   hand-written patient-context warnings and does **not** re-add candidates the model rejected.

### 6.3 Layer 2 path (clinical reviewer)
`app_server.AuditService.review_section(payload)`:
1. Builds the same row as layer 1.
2. Collects **hints**: runs the orchestrator with a stand-in provider (`_Recorder`) that records candidates and
   decides nothing; `clinical_reviewer.concrete_hints` keeps only candidates that state a finding (drops the generic
   "Review whether…" prompts).
3. Drug facts for the `rx` section.
4. `clinical_reviewer.build_messages(row, section, drug_facts, hints, already=layer1)` → chat messages (§7.5).
5. `provider.review(messages, 600, prefix='{"entries": [', stop="]}]}")`: the base model (adapter disabled) answers
   **entry by entry**; the answer is started for it with the JSON prefix so it cannot drift into prose.
6. `parse_problems` (tolerant JSON reader: first complete JSON object) → `in_section` (drop problems whose entry is
   not in this section) → `split_shown` (type gating, §7.5) → **second look**: `build_verify_messages` asks the same
   model which listed problems a senior physician would really ask to correct (`{"keep": [...]}`); only kept ones
   are shown.
7. Response: `problems` (shown) and `hidden` (other types, for inspection). With `"debug": true` in the payload the
   raw answer and the proposed list are included.

### 6.4 Final audit
`POST /api/audit` runs the writer-time checks over the complete prescription and builds the final report
(`final_report.py`); the frontend saves it as `09_aiAudit.json`. Rejected high-evidence candidates are kept in
metadata but, in model-only mode, do not change the status.

---

## 7. The model

### 7.1 Base model and loading
- Base: **Gemma 4 12B instruct**, `unsloth/gemma-4-12B-it-qat-q4_0-unquantized` (Hugging Face), loaded in 16-bit
  (`HF_QUANT=none`, ~24 GB of GPU memory).
- Layer-1 adapter: QLoRA adapter `outputs/unsloth__gemma-4-12B-it-qat-q4_0-unquantized__mapping_v4/adapter_gold`,
  loaded with PEFT on top of the base (`HF_ADAPTER`). Prompt mode forced to fine-tune (`AUDIT_PROMPT_MODE=finetune`).
- Layer 2 uses the same object with `model.disable_adapter()`; a re-entrant GPU lock (`LocalChatProvider._gpu_lock`)
  ensures a layer-1 call never runs while the adapter is disabled.
- Note: an unmerged adapter is slower than a merged model (layer-1 median 1.8 s vs 1.3 s merged; ~16% of layer-1
  checks over 6 s). Merged copies exist in each run's `merged/` folder.

### 7.2 Layer-1 prompt format (training format, Alpaca-style)
```
### Instruction:
Review only the Chief complaint section. Select every matching CC error type from the allowed_error_types list.
Do not invent rules. Return JSON only with has_error, section, and detected_errors. Each detected error must contain
rule_id, rule_name, concern, and why: one short sentence naming the entry in this prescription that is wrong and what
is wrong or missing about it.

### Input:
{"section": "CC", "section_name": "Chief complaint",
 "allowed_error_types": [{"rule_id": "CC.1", "rule_name": "...", "concern": "..."}, ...],
 "prescription_context": {"Age": "49", "Gender": "M",
                          "Chief_Complaint": "Pregnancy for 1 month\nEarache for 3 days\nAbdominal Pain"}}

### Output:
```
The input is `json.dumps(..., ensure_ascii=False)`. The instruction text and rule list come from the taxonomy file;
the prompt the app sends must be identical to the training prompts ("prompt parity", checked at 100%).

### 7.3 Layer-1 answer format
```json
{"has_error": true, "section": "CC",
 "detected_errors": [
   {"rule_id": "CC.demo", "rule_name": "chief_complaint_demographic_mismatch",
    "concern": "<the taxonomy concern text>",
    "why": "'Pregnancy' does not fit a male patient: check the complaint and the recorded sex."},
   {"rule_id": "CC.3", "rule_name": "duration_missing", "concern": "...",
    "why": "Duration is not mentioned for 'Abdominal Pain'."}]}
```
Errors are listed in taxonomy order; each rule appears at most once per answer (one reason why a second item of the
same kind may go unreported — layer 2 and per-line prompts compensate).

### 7.4 Drug facts in the Rx prompt
`prescription_context.Drug_Facts` is the multi-line text of §4.2. The model was trained to raise `Rx.risk.pregnancy`,
`Rx.contra`, `Rx.ddi`, `Rx.dup` and `Rx.over` from what these lines show (labels were made consistent with the
visible facts).

### 7.5 Layer-2 prompt and answer (clinical reviewer)
**System:** "You are an experienced general physician in Bangladesh checking another doctor's prescription while it
is being written. You report only real problems that a careful senior colleague would point out, you state medical
facts only when you are sure of them, and you never invent problems."

**User message** (built by `clinical_reviewer.build_messages`), in this order:
1. The prescription written so far, entries numbered per section; context lines (screening, examination, results);
   "(none written)" for empty earlier sections.
2. Drug facts (Rx only); rule-engine hints ("may be wrong; confirm only real problems"); "Already reported by the
   first check (do not repeat)".
3. The task: which section to check; the **clinic documentation standard** (a body part that exists on both sides or
   more than once must state the side; severity only for symptoms where it decides emergency care; antibiotic course
   over 14 days without a reason → review); what to report; what **not** to report (sections not written yet,
   results not back, formatting, diagnosis completeness, dose comments except adult dose for a child and the antibiotic
   rule, anything layer 1 already reported); severities; **types**; and the entry-by-entry answer form.

**Answer:**
```json
{"entries": [
  {"n": 1, "entry": "Pregnancy for 1 month",
   "problems": [{"problem": "Pregnancy is not possible for a male patient.", "severity": "critical", "type": "sex_age_anatomy"}]},
  {"n": 2, "entry": "Earache for 3 days",
   "problems": [{"problem": "The side of the ear is not specified.", "severity": "documentation", "type": "side_missing"}]},
  {"n": 3, "entry": "Abdominal Pain", "problems": []}]}
```
**Types** (the model picks the first that fits): `sex_age_anatomy`, `side_missing`, `severity_missing`,
`contradiction`, `implausible`, `no_reason`, `unexplained`, `unsafe_for_patient`, `antibiotic_duration`,
`interaction`, `missing_care`, `dose`, `other`.
**Shown types** (`SHOWN_TYPES`): `sex_age_anatomy`, `side_missing`, `severity_missing` (complaints section only),
`contradiction`, `implausible`, `unsafe_for_patient`, `antibiotic_duration`. The others are returned as `hidden`
because in testing they were mostly wrong on correct prescriptions (§9.4); layer 1 covers those areas.

**Design reasons:** answering entry by entry stopped the model from skipping entries when extra context (vital
signs) was present; the JSON prefix stopped it from reasoning in prose until the token limit (21 s, answer lost);
the tolerant reader recovers answers with an extra closing bracket; the second look removed most remaining false
alarms without hand-written filters.

---

## 8. Training

### 8.1 Method
- Framework: Unsloth + TRL (`train.py`), QLoRA (4-bit base during training), LoRA rank 16, alpha 32, max length 3,072
  tokens (longest example 2,486), batch 1 × gradient accumulation 16, learning rate 1e-4, 2 epochs, seed 3407,
  evaluation every 50 steps, best checkpoint by validation loss kept, merged to 16-bit at the end.
- Two stages:
  1. **Silver stage** (done once): adapter `outputs/unsloth__gemma-4-12B-it-qat-q4_0-unquantized__qlora4bit/adapter_silver`,
     trained on the 13,939-prescription silver set.
  2. **Gold stage** (repeated per version): starts from the silver adapter, trains on the gold training file
     (`GOLD_DATA`), validates on `VAL_FILE`. Only this stage is rerun for new versions.
- Commands:
  - train: `cd C:\Users\HEAT\Documents\LLM_Trainer; py -3.11 train.py --env mapping_v5.env`
  - resume after an interruption: add `--resume` (continues from the last checkpoint)
  - score: `.\after_training_check.ps1` (writes `after_training_check.log`)
  - install: `cd "C:\Users\HEAT\Documents\Local Pipeline\Local_pip"; .\install_model.ps1` then `.\run_local.ps1`;
    undo with `.\install_model.ps1 -Undo`. The installer sets `HF_ADAPTER` (keeps the base model, so layer 2 works)
    and copies the matching taxonomy (`finetune_taxonomy_v7.json`).

### 8.2 Training data (current: v7 files, used by mapping_v5)
`LLM_Trainer/data/gold_v7_knowledge_train.jsonl` — 5,953 rows; validation 582 rows. Each row:
```json
{"task_type": "section_error_classification", "target_section": "CC", "prescription_id": "...",
 "label_source": "...", "instruction": "...", "input": {"section": "...", "section_name": "...",
 "allowed_error_types": [...], "prescription_context": {...}}, "output": {"has_error": ..., "section": ...,
 "detected_errors": [{"rule_id", "rule_name", "concern", "why"}]}}
```
Composition of the training file:

| `label_source` | Rows | Share | Origin |
|---|---|---|---|
| `multi_physician_gold_v2` | 1,764 | 30% | 147 physician-labelled real prescriptions, one row per section, counted twice for weight |
| `physician_mapping_review_v1` | 68 | 1% | 6 more physician-reviewed prescriptions |
| `synthetic_knowledge_v1` / `_v5` | 1,786 | 30% | generated sex/age/time/duplicate/detail cases (`build_knowledge_data.py`) on real layouts |
| `synthetic_drugfacts_v1` | 1,011 | 17% | a real silver prescription + one added medicine, labelled from the physician's 69 drug-fact decisions and the drug database |
| `synthetic_lessons_v1` | 968 | 16% | lesson scenarios for rare rules (`lessons_v7.py`), written by an AI assistant and corrected by an AI-assisted 153-item review, plus round-2 fixes from live tests |
| `synthetic_imci_v1` | 356 | 6% | under-5 IMCI lessons (`imci_lessons.py`) from the Bangladesh IMCI Student's Handbook 2019, corrected by an AI-assisted review |

About 31% of rows are physician-labelled real data; the rest is synthetic. The "why" sentences come from physician
notes where present, otherwise from templates, plus 57 written by an AI assistant.

### 8.3 Data-building pipeline (run in order with the pipeline's Python for v6/v7)
1. `build_knowledge_data.py` → knowledge examples (held-out words kept out of training for the test set).
2. `add_physician_mappings.py` → physician mapping-review findings (gold v4).
3. `build_v5_data.py` → corrected rule descriptions, impossible-timing rows, physician verdict labels (gold v5).
4. `build_v6_drugfacts_data.py` → Drug_Facts, pregnancy (trimester-aware) and contraindication examples with
   counter-examples (gold v6).
5. `build_v7_explanations.py` → v7: "why" on every error, and all later fixes:
   - **consistent meal timing** (`fix_timing`): label = an oral medicine without meal timing; ~half the medicine rows
     get the app's Bangla timing notes so "timing written" is learnable;
   - **column repair** (`unshift`): prescriptions P0201–P0220 had rotated columns (medicines in the complaint field);
     restored from `Gold_Mapping_Review.csv`;
   - **duplicate lesson diversified** (`diversify_dup`): was one pair (Napa + Ace) ×111, now 96 real same-generic
     brand pairs (validation uses unseen generics);
   - **Rx.cc.mis relabel** per the clinical review;
   - **lessons** (`lessons_v7.build`, run twice for training) and **IMCI lessons** (`imci_lessons.build`);
   - **base-medicine labels** (`label_base_meds`): pregnancy risk and duplicates of medicines already in the
     prescription, read from the same drug facts the model sees (75 + 22 added);
   - **app line format** (`line_lists`): half of the generated complaint/history rows written one entry per line, as
     the app sends them;
   - Drug_Facts rebuilt for every Rx row with the current mapper; explanations attached; errors ordered.
6. Checks before training (scratch `final_check.py`): instruction and rule list match the taxonomy, app prompt ==
   training prompt for every row (100%), token lengths under MAX_LEN, no identical input with different answers, no
   validation/test input present in training.

### 8.4 Model versions
| Version | Data | Result / status |
|---|---|---|
| knowledge_v1 | gold v3 | earlier model |
| mapping_v1 | gold v4 | installed until 2026-11-14 |
| mapping_v2 | — | tested, worse at catching than v1 |
| mapping_v3 | v6 | stopped (PC power cuts) |
| **mapping_v4** | v7 (first round) | **installed** (adapter). Best checkpoint step 300 of 720, validation loss 0.0359 |
| mapping_v5 | v7 round 2 (5,953 rows) | training interrupted by a power cut at step 200 of 746 (val loss 0.042); resumable with `--resume`; not installed |

---

## 9. Evaluation

### 9.1 Test sets
| File | Rows | What it tests |
|---|---|---|
| `gold_v7_knowledge_validation.jsonl` | 560–582 | held-out physician rows and held-out lesson scenarios |
| `drugfacts_v7_test.jsonl` | 90 | drug-fact judgements on drugs not in training |
| `knowledge_v7_test.jsonl` | 148 | held-out sex/age/time/duplicate/detail terms (e.g. "Weight loss for 1 day") |
| `benchmark/live_suite.py` | 13 cases | end-to-end through the running app, all sections, scored caught/missed/wrong |
| `benchmark/doctor_probe.py` | 15 cases | the project doctor's usability findings |
| CLEAN set in `benchmark/clinical_reviewer_prototype.py` | 8 | correct prescriptions: any alert is a false alarm |
| `benchmark/live_review_probe.py` | 36 cases | both layers end to end (doctor 15 + suite 13 + clean 8) |

Scorer: `eval_knowledge_test.py <model> --data=<file>` — a row is "exact" when the predicted rule set equals the
label set; prints caught/total and false alarms per rule.

### 9.2 mapping_v4 results (training-format scoring)
| Test | Result |
|---|---|
| Validation, exact match | 477/560 (85%) |
| Drug-fact test, exact | 72/90 (80%) |
| Knowledge test, exact | 142/148 (96%) (mapping_v1: 144/148; v4 fixed "weight loss for 1 day", lost "pregnancy at 70") |
| Per rule (validation + tests) | CC.3 35/35; Hx rules all perfect; Dx.inv.1 5/5; Adv.refer 11/12; Rx.timing 125/131; Rx.risk.pregnancy 29/29; Rx.contra 8/8; Rx.dup 9/9 |
| Weak | Rx.ddi on unseen pairs 0/3; Rx.demo 0/6; Rx.cc.sym 1/6; Dx.hs.1 0/4 |
| False alarms | about half were real problems the test labels missed (pregnancy-unsafe or duplicate medicines already in the prescription) — fixed in v5 data |

### 9.3 Live results
- Live suite, mapping_v1 → mapping_v4: caught 8 → 11, missed 4 → 1, wrong 0 → 3 (4-bit); in 16-bit: caught 10,
  missed 2, wrong 1, median 1.6 s.
- Model-only mode (engine alerts off): caught 9, missed 3, wrong 1, median 1.3 s (merged model).
- Remaining layer-1 wrong alerts: `Rx.cc.sym` "no antibiotic prescribed" although Ciprocin is prescribed (training data
  had almost no "treatment present" examples → added in v5).

### 9.4 Two-layer results (final run, `benchmark/results/live_review_probe_v7.log`)
- All 15 doctor-reported cases flagged by at least one layer: missing side (elbow, ear), left/right contradiction
  across complaint/X-ray/diagnosis, chest and abdominal pain severity, PSA for a woman and Pap smear for a man
  (critical), pregnancy/GDM/vaginal cramp in a man (critical), 30-day antibiotic, adult paracetamol dose for a
  4-year-old (critical), weight change in 1 day, hysterectomy in a man, history longer than life.
- Reviewer false alarms on the 8 correct prescriptions: 0.
- Latency: layer 1 median 1.8 s (slowest 12.1 s); layer 2 median 2.2 s (slowest 8.7 s).
- Tuning history: initial reviewer ~10 false alarms on 8 correct prescriptions → second look → type gating →
  entry-by-entry → 0. Type gating evidence: `interaction`, `unexplained`, `no_reason`, `dose`, `missing_care` were
  mostly wrong (e.g. "ciprofloxacin with antacids" when none prescribed, "metformin XR?", "paracetamol 1 g/day
  sub-therapeutic").

### 9.5 Other findings
- Writing order does not affect layer 1: the same section with a partial or complete prescription gave identical
  answers (`benchmark/order_probe.py`).
- The app's one-entry-per-line format was absent from training; joining lines with commas in the prompt made results
  worse (the model read "pregnancy for two days, vaginal cramp, fever" as one entry with a duration), so the fix was
  done in training data (§8.3).

### 9.6 Cloud reviewer comparison
Groq `qwen/qwen3.8-27b` as reviewer on the same cases (`benchmark/cloud_reviewer_compare.py`): 0 false alarms but
missed the adult dose for a child, the 30-day antibiotic and the antibiotic with no infection; the free tier
(8,000 tokens/minute) cannot serve live use. Not adopted.

---

## 10. Configuration (`Local_pip/.env`)
| Key | Current | Meaning |
|---|---|---|
| `AUDIT_PROVIDER` | `local` | in-process model |
| `HF_MODEL` | `unsloth/gemma-4-12B-it-qat-q4_0-unquantized` | base model (must stay the base for layer 2) |
| `HF_ADAPTER` | `.../mapping_v4/adapter_gold` | layer-1 adapter |
| `AUDIT_PROMPT_MODE` | `finetune` | layer-1 prompt format |
| `HF_QUANT` | `none` | 16-bit (`4bit` uses less memory, slower decoding) |
| `FINETUNE_QUESTION_RULES` | `none` | extra yes/no questions (off: slow, caught nothing extra) |
| `FINETUNE_MODEL_FIRST_EXCLUDE` | `none` | rules the model may not raise without a candidate |
| `FINETUNE_UNSUPPORTED` | `omit` | rules outside the taxonomy are dropped (was `confirm_high`: engine decided) |
| `AUDIT_MODEL_ONLY` | `1` | no hand-written warnings; rejected candidates not re-added |
| `AUDIT_REVIEWER` | `1` (default) | `0` turns layer 2 (clinical reviewer) off |
| `AUDIT_LOG_LEVEL` | `INFO` (default) | `DEBUG` also logs every HTTP request |
| `FINETUNE_FAST_CONCERNS` | default on | fast-concern decoding |
| `FINETUNE_MODEL_FIRST` | default on | model may raise rules without a candidate |
| `GROQ_API_KEY`, `GROQ_MODEL` | set | used only for the comparison test |

Backups: `.env.before_reviewer` (before the two-layer switch), `.env.before_install` (before the last install).
Always start with `run_local.ps1` (reads `.env` fresh; a stale shell can keep old variables).

---

## 11. Operations

### 11.1 Start and stop
- Start: `cd "C:\Users\HEAT\Documents\Local Pipeline\Local_pip"; .\run_local.ps1` (server ready in ~30 s).
- Stop the server: `Get-NetTCPConnection -LocalPort 8765 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }`.
- The app (24 GB GPU) and training (~12 GB) cannot run at the same time on the 32 GB GPU.

### 11.2 Test commands (app must be running; nobody else using it)
`python -m benchmark.live_suite` · `python -m benchmark.doctor_probe` · `python -m benchmark.live_review_probe`
(use `.venv\Scripts\python.exe`). The prototype and cloud comparison need the app stopped / a Groq key.

### 11.3 Server logs
`audit_server.log` contains timing lines: `[finetune] section=CC prompts=5 rounds=3 fallbacks=0 took=11.1s ...` and
`[review] section=... proposed=N hidden_types=N shown=N took=...`.

### 11.4 Known environment issues
- **Power cuts**: several unexpected shutdowns (Kernel-Power 41, bugcheck 0), at load and at idle → PSU or wall power.
  Use checkpoints/resume; a UPS and PSU check are recommended before multi-day jobs.
- **Smart App Control** blocked `torch.dll`, pandas `.pyd` and Triton caches → turned off.
- Windows console QuickEdit pauses training when the window is clicked.

---

## 12. Limitations and next steps

### 12.1 Current limitations
1. Layer 1 knows only what it was trained on; about 70% of training data is synthetic; some rules have no examples.
2. Layer 2 is a 12B general model: reliable for 7 types only; its dose, interaction and diagnosis-completeness
   opinions are hidden.
3. Guideline facts are not given to the model; retrieval is context-blind.
4. Layer-1 latency: ~16% of checks over 6 s with the unmerged adapter.
5. Reviews of the lessons and IMCI checks were AI-assisted, not line-by-line physician validation.
6. Not yet tested by doctors on real prescriptions at scale.

### 12.2 Agreed plan (to start after 2026-11-19): local distillation from 700K prescriptions
No cloud API and no new physician time are available, so:
1. Keep the 200 physician-labelled prescriptions strictly as the **test set**.
2. Label 30–50K sampled prescriptions overnight with the **local reviewer** (base 12B, entry by entry, reliable types),
   keeping labels where independent signals agree; resumable, checkpointed.
3. Learn **practice norms by counting** over all 700K prescriptions (dose/duration per generic and age band,
   medicine–diagnosis co-occurrence) and create "unusual, check this" examples from them — data-derived, not
   hand-written.
4. Mix ~15% medical Q&A generated locally from the Bangladesh guidelines to keep medical knowledge.
5. Train one clean model (12B, QLoRA) in a **per-entry output format** (problem, type, severity, why) — one model
   replacing both layers.
6. Evaluate on the 200 physician prescriptions and the doctor cases; install only if better; collect 👍/👎 feedback
   from doctors' normal use.

---

## 13. Glossary
- **Candidate**: a possible error proposed by a rule engine; only a model decides whether it is shown.
- **Gold / silver**: physician-labelled data / automatically (GPT + RAG) labelled data.
- **QLoRA adapter**: small trained weights added to a frozen 4-bit base model; can be switched on or off.
- **Prompt parity**: training prompts and live prompts are byte-identical in structure and wording.
- **Model-first**: the model may report a rule even if no candidate proposed it.
- **Type gating**: layer 2 shows only finding types it judges reliably.
- **Second look**: layer 2 re-judges its own findings before they are shown.
- **Drug_Facts**: database lines about each prescribed medicine, shown to the model.
- **IMCI**: Integrated Management of Childhood Illness (Bangladesh 2019 handbook).
