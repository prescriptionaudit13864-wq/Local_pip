"""Does asking sex/age and timing as plain YES/NO questions recover what the fine-tune misses, without new
false alarms?

Uses the app's own model class (LocalChatProvider) and question code (build_knowledge_question, finding_from_answer),
so the questions are asked exactly as the live audit asks them.

    python -m benchmark.question_backup_test <merged model folder> <knowledge test results json> [LLM_Trainer/data]

1. The 148-case knowledge test: CC rows only. The fine-tune's own answer (from the results json) is combined
   with YES answers to CC.context.demo and CC.time.plausibility, and scored against the label.
2. The real complaints in the 32 gold validation prescriptions: none is labelled CC.demo or CC.time, so every
   YES here is a false alarm the backup would add in the live app.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

MODEL, RESULTS = sys.argv[1], Path(sys.argv[2])
DATA = Path(sys.argv[3] if len(sys.argv) > 3 else r"C:\Users\HEAT\Documents\LLM_Trainer\data")
os.environ.update({"AUDIT_PROVIDER": "local", "HF_MODEL": MODEL, "HF_QUANT": "4bit", "LLM_BASE_URL": ""})

from audit_pipeline.finetune_prompt import build_knowledge_question, finding_from_answer  # noqa: E402
from audit_pipeline.provider import _KNOWLEDGE_SYSTEM, LocalChatProvider  # noqa: E402

CHECKS = {"CC.context.demo": "CC.demo", "CC.time.plausibility": "CC.time"}
provider = LocalChatProvider(model=MODEL)
asked: dict[str, str] = {}


def flags(entry: str, age: str, gender: str) -> tuple[set[str], list[str]]:
    """The trained rule ids the YES/NO questions confirm for one complaint entry, and the raw answers."""
    found, answers = set(), []
    for pipeline_rule, trained in CHECKS.items():
        candidate = {"rule_id": pipeline_rule, "evidence": f"Entered chief complaint: {entry}", "candidate_id": "x"}
        question = build_knowledge_question(candidate, {"Age": age, "Gender": gender})
        if question not in asked:
            asked[question] = provider._generate(_KNOWLEDGE_SYSTEM, question, 60, "")
        answers.append(f"{trained}: {asked[question].strip()[:120]}")
        if finding_from_answer(candidate, asked[question], question):
            found.add(trained)
    return found, answers


def main() -> None:
    rows = json.loads(RESULTS.read_text(encoding="utf-8"))
    cc = [r for r in rows if set(r["want"]) <= {"CC.demo", "CC.time"} and not any(w.startswith("Hx") for w in r["want"])
          and "Hx" not in r["id"]]
    before = after = 0
    changes = []
    for r in cc:
        want, got = set(r["want"]), set(r["got"] or [])
        extra, answers = flags(r["entry"], str(r["age"]), r["gender"])
        combined = got | extra
        before += got == want
        after += combined == want
        if combined != got:
            changes.append((r["entry"], r["age"], r["gender"], sorted(want), sorted(got), sorted(combined), answers))
    print(f"knowledge test, CC rows: {len(cc)} | fine-tune alone {before} correct | with YES/NO backup {after} correct")
    for c in changes:
        print("  changed:", c[0], c[1], c[2], "want", c[3], "model", c[4], "->", c[5])
        for a in c[6]:
            print("     ", a)

    real = [json.loads(line) for line in open(DATA / "gold_v5_knowledge_validation.jsonl", encoding="utf-8")]
    real = [r for r in real if r["label_source"] == "multi_physician_gold_v2" and r["target_section"] == "CC"]
    alarms, items = [], 0
    for r in real:
        ctx = r["input"]["prescription_context"]
        for entry in [e.strip() for e in str(ctx.get("Chief_Complaint", "")).split(",") if e.strip()]:
            items += 1
            extra, answers = flags(entry, str(ctx.get("Age", "")), str(ctx.get("Gender", "")))
            if extra:
                alarms.append((r["prescription_id"], entry, ctx.get("Age"), ctx.get("Gender"), sorted(extra), answers))
    print(f"\nreal complaints: {items} entries in {len(real)} prescriptions | backup false alarms: {len(alarms)}")
    for a in alarms:
        print("  ", a[:5])
        for line in a[5]:
            print("     ", line)


if __name__ == "__main__":
    main()
