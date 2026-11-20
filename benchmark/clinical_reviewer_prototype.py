"""Test of the clinical reviewer (audit_pipeline/clinical_reviewer.py), section by section as the app runs it.

The reviewer is the base Gemma 4 model: the v4 fine-tune adapter is loaded and switched off for these calls, so one
copy of the weights serves both layers. For every case, each written section is reviewed with only the sections
written up to it, as in the app.

Cases: the project doctor's 15 (benchmark/doctor_probe.py), the 13 live-suite cases, and 8 correct prescriptions
(CLEAN below) that should raise nothing above "documentation".

    python -m benchmark.clinical_reviewer_prototype        (stop the app first: the model needs the GPU memory;
                                                            run with the training Python, which has peft)
Writes benchmark/results/clinical_reviewer_prototype.json.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

_args, sys.argv[1:] = sys.argv[1:], []
from benchmark import doctor_probe, live_suite  # noqa: E402
from benchmark.live_suite import med  # noqa: E402

sys.argv[1:] = _args
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "benchmark" / "results" / "clinical_reviewer_prototype.json"
BASE = "unsloth/gemma-4-12B-it-qat-q4_0-unquantized"
ADAPTER = (
    r"C:\Users\HEAT\Documents\LLM_Trainer\outputs\unsloth__gemma-4-12B-it-qat-q4_0-unquantized__mapping_v4\adapter_gold"
)
CODE = {
    "chiefComplaints": "CC",
    "pastHistory": "Hx",
    "investigations": "Inv",
    "diagnosis": "Dx",
    "rx": "Rx",
    "advice": "Adv",
}

# Correct prescriptions: anything above "documentation" here is a false alarm.
CLEAN = [
    (
        "Clean: adult cystitis",
        "Female",
        28,
        {
            "chiefComplaints": ["Burning micturition for 3 days", "Increased frequency of micturition for 3 days"],
            "diagnosis": ["Acute cystitis"],
            "rxList": [
                med("Ciprocin", "Ciprofloxacin", "500 mg", "1+0+1", "5 days"),
                med("Napa", "Paracetamol", "500 mg", "1+1+1", "3 days"),
            ],
            "adviceList": [
                "Drink plenty of water",
                "Complete the full course of antibiotic",
                "Come back if fever or back pain",
            ],
        },
    ),
    (
        "Clean: child common cold",
        "Male",
        3,
        {
            "chiefComplaints": ["Runny nose for 2 days", "Cough for 2 days"],
            "diagnosis": ["Common cold"],
            "rxList": [med("Napa", "Paracetamol", "120 mg/5 ml", "1 tsp thrice daily", "3 days", form="Syrup")],
            "adviceList": [
                "Give plenty of fluids",
                "Come back immediately if breathing becomes fast or the child cannot drink",
                "Follow up in 5 days if not improving",
            ],
        },
    ),
    (
        "Clean: gastritis",
        "Male",
        35,
        {
            "chiefComplaints": ["Epigastric pain for 1 month", "Heartburn for 1 month"],
            "diagnosis": ["Gastritis"],
            "rxList": [med("Seclo", "Omeprazole", "20 mg", "1+0+1", "14 days", instruction="Before meal")],
            "adviceList": ["Avoid oily and spicy food", "Eat meals on time"],
        },
    ),
    (
        "Clean: hypertension follow-up",
        "Male",
        55,
        {
            "chiefComplaints": ["Follow up of hypertension"],
            "pastHistory": ["Hypertension for 5 years"],
            "diagnosis": ["Essential hypertension"],
            "rxList": [med("Angitan", "Losartan", "50 mg", "1+0+0", "30 days")],
            "adviceList": ["Reduce salt", "Walk 30 minutes daily", "Check blood pressure regularly"],
        },
    ),
    (
        "Clean: new type 2 diabetes",
        "Male",
        50,
        {
            "chiefComplaints": ["Increased thirst for 1 month", "Frequent urination for 1 month"],
            "investigations": ["Fasting blood sugar", "HbA1c"],
            "diagnosis": ["Type 2 diabetes mellitus"],
            "rxList": [med("Comet", "Metformin", "500 mg", "1+0+1", "30 days")],
            "adviceList": ["Diabetic diet", "Regular exercise", "Follow up after 1 month with blood sugar report"],
        },
    ),
    (
        "Clean: scabies",
        "Female",
        20,
        {
            "chiefComplaints": ["Itching all over the body worse at night for 2 weeks"],
            "diagnosis": ["Scabies"],
            "rxList": [
                med(
                    "Lorix",
                    "Permethrin",
                    "5%",
                    "Apply from neck to toe at night",
                    "1 day",
                    instruction="Wash off next morning",
                    form="Cream",
                ),
                med("Alatrol", "Cetirizine", "10 mg", "0+0+1", "7 days"),
            ],
            "adviceList": ["Treat all family members at the same time", "Wash clothes and bedding in hot water"],
        },
    ),
    (
        "Clean: left knee osteoarthritis",
        "Female",
        60,
        {
            "chiefComplaints": ["Pain in left knee for 6 months"],
            "investigations": ["X-ray of left knee"],
            "diagnosis": ["Osteoarthritis of left knee"],
            "rxList": [med("Napa", "Paracetamol", "500 mg", "1+0+1", "7 days")],
            "adviceList": ["Reduce weight", "Quadriceps exercise", "Avoid squatting"],
        },
    ),
    (
        "Clean: first antenatal visit",
        "Female",
        25,
        {
            "chiefComplaints": ["Pregnancy 12 weeks, first antenatal visit"],
            "investigations": ["CBC", "Blood grouping", "HBsAg", "VDRL", "Urine R/E", "Random blood sugar"],
            "diagnosis": ["Primigravida with 12 weeks of pregnancy"],
            "rxList": [med("Feofol", "Ferrous sulphate + Folic acid", "150 mg", "0+0+1", "3 months")],
            "adviceList": [
                "Attend antenatal check-ups",
                "Come immediately if bleeding, severe headache or reduced fetal movement",
            ],
        },
    ),
]


def build_row(sex: str, age: int, entries: dict, upto: str) -> dict[str, str]:
    from audit_pipeline.app_adapter import visit_to_row

    patient = {
        "id": 990000,
        "serial": "PROTO",
        "name": "Prototype",
        "gender": sex,
        "dob": date(date.today().year - age, 1, 1).isoformat(),
        "ageValue": age,
        "ageUnit": "years",
    }
    visit: dict[str, Any] = {"id": 1799200000000}
    for section, key in live_suite.ORDER:
        if key in entries:
            value = entries[key]
            visit[key] = value if key == "rxList" else [{"id": i + 1, "text": t} for i, t in enumerate(value)]
        if section == upto:
            break
    return visit_to_row(patient, visit, datetime.now())


class HintRecorder:
    def __init__(self) -> None:
        self.tasks: list[dict[str, Any]] = []

    def audit(self, *, system: str, prompt: str, max_tokens: int) -> dict:  # noqa: ARG002
        from audit_pipeline.finetune_prompt import parse_section_task

        task = parse_section_task(prompt)
        self.tasks.append(task)
        return {"section": task.get("section", ""), "findings": []}


def hints_for(row: dict[str, str], section_key: str) -> list[str]:
    from audit_pipeline.clinical_reviewer import concrete_hints
    from audit_pipeline.finetune_prompt import flatten_candidates
    from audit_pipeline.orchestrator import AuditOptions, audit_prescription

    rec = HintRecorder()
    try:
        audit_prescription(row, AuditOptions(provider=rec, include_all_sections=True, max_retries=0))
    except Exception:
        return []
    cands = [c for t in rec.tasks for c in flatten_candidates(t.get("candidates"))]
    return concrete_hints(cands, CODE[section_key])


def load_model():
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(BASE)
    base = AutoModelForCausalLM.from_pretrained(BASE, dtype=torch.bfloat16, device_map="cuda")
    model = PeftModel.from_pretrained(base, ADAPTER)
    model.eval()
    return tok, model


def review(tok, model, messages) -> tuple[str, float]:
    import torch

    ids = tok.apply_chat_template(messages, add_generation_prompt=True, return_tensors="pt", return_dict=True).to(
        model.device
    )
    started = time.perf_counter()
    with torch.inference_mode(), model.disable_adapter():
        out = model.generate(**ids, max_new_tokens=400, do_sample=False)
    return tok.decode(out[0][ids["input_ids"].shape[1] :], skip_special_tokens=True), time.perf_counter() - started


def main() -> None:
    from audit_pipeline.clinical_reviewer import build_messages, build_verify_messages, parse_keep, parse_problems
    from audit_pipeline.finetune_prompt import _drug_facts

    cases = (
        [("doctor", t, s, a, e) for t, s, a, e in doctor_probe.CASES]
        + [("suite", t, s, a, e) for t, s, a, e, _x, _g in live_suite.CASES]
        + [("clean", t, s, a, e) for t, s, a, e in CLEAN]
    )
    print("loading base model + adapter ...", flush=True)
    tok, model = load_model()
    review(tok, model, [{"role": "user", "content": "Say OK."}])  # warm-up
    results, times = [], []
    for n, (group, title, sex, age, entries) in enumerate(cases, 1):
        print(f"\n=== {n}. [{group}] {title} ({sex}, {age} y)", flush=True)
        for section_key, list_key in live_suite.ORDER:
            if list_key not in entries:
                continue
            row = build_row(sex, age, entries, section_key)
            facts = _drug_facts(row) if section_key == "rx" else ""
            messages = build_messages(row, section_key, drug_facts=facts, hints=hints_for(row, section_key))
            text, secs = review(tok, model, messages)
            proposed = parse_problems(text)
            kept, vsecs = proposed, 0.0
            if proposed:
                vtext, vsecs = review(tok, model, build_verify_messages(row, section_key, proposed, facts))
                keep = parse_keep(vtext, len(proposed))
                kept = [p for i, p in enumerate(proposed, 1) if i in keep]
            times.append(secs + vsecs)
            results.append(
                {
                    "n": n,
                    "group": group,
                    "case": title,
                    "section": section_key,
                    "secs": round(secs + vsecs, 1),
                    "proposed": proposed,
                    "problems": kept,
                    "raw": text,
                }
            )
            print(f"  {section_key} ({secs + vsecs:.1f}s){'  -' if not proposed else ''}", flush=True)
            for p in proposed:
                mark = "KEEP" if p in kept else "drop"
                print(f"      {mark} {p['severity'][:4]:4} | {p['entry'][:40]} -> {p['problem'][:140]}", flush=True)
        OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    times.sort()
    print(f"\nTIME per section review: median {times[len(times) // 2]:.1f}s, slowest {times[-1]:.1f}s, n={len(times)}")


if __name__ == "__main__":
    os.chdir(ROOT)
    main()
