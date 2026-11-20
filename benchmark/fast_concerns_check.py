"""Check that writing known concern text (FINETUNE_FAST_CONCERNS) gives the same rules as normal decoding.

    python -m benchmark.fast_concerns_check <merged model folder> [n rows] [LLM_Trainer/data]

Runs the app's LocalChatProvider on gold validation prompts (rows with findings first) both ways and reports,
per row, the rule ids each way and the time each took. Prints the raw answers of the first mismatch.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

MODEL = sys.argv[1]
N = int(sys.argv[2]) if len(sys.argv) > 2 else 24
DATA = Path(sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] != "-" else r"C:\Users\HEAT\Documents\LLM_Trainer\data")
SECTIONS = set(sys.argv[4].split(",")) if len(sys.argv) > 4 else None  # e.g. Dx,Adv
os.environ.update({"AUDIT_PROVIDER": "local", "HF_MODEL": MODEL, "HF_QUANT": "4bit", "LLM_BASE_URL": "",
                   "FINETUNE_QUESTION_RULES": "none"})

from audit_pipeline.finetune_prompt import detected_rule_map  # noqa: E402
from audit_pipeline.provider import LocalChatProvider, parse_json_value, repair_json_object  # noqa: E402


def ids(text: str) -> set[str]:
    try:
        payload = parse_json_value(text)
    except ValueError:
        payload = repair_json_object(text)
    return set(detected_rule_map(payload)) if payload is not None else {"<unreadable>"}


def main() -> None:
    with open(DATA / "gold_v4_knowledge_validation.jsonl", encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh]
    rows = [r for r in rows if r["label_source"] == "multi_physician_gold_v2"
            and (SECTIONS is None or r["target_section"] in SECTIONS)]
    rows.sort(key=lambda r: -len(r["output"]["detected_errors"]))
    rows = rows[:N]
    provider = LocalChatProvider(model=MODEL)
    same = 0
    shown = False
    t_normal = t_fast = 0.0
    for r in rows:
        text = (f"### Instruction:\n{r['instruction']}\n\n### Input:\n{json.dumps(r['input'], ensure_ascii=False)}"
                "\n\n### Output:\n")
        t0 = time.perf_counter()
        normal = provider._generate("", text, 512, "", raw=True)
        t1 = time.perf_counter()
        fast = provider._answer_finetune([text], 512, r["target_section"])[0]
        t2 = time.perf_counter()
        t_normal += t1 - t0
        t_fast += t2 - t1
        a, b = ids(normal), ids(fast)
        same += a == b
        print(f"{r['prescription_id']} {r['target_section']}: normal {sorted(a)} {t1 - t0:.1f}s | fast {sorted(b)} {t2 - t1:.1f}s"
              f"{'' if a == b else '   <-- DIFFERENT'}", flush=True)
        if a != b and not shown:
            shown = True
            print("   NORMAL:", normal[:600].replace("\n", " "))
            print("   FAST:  ", fast[:600].replace("\n", " "))
    print(f"\nsame rules: {same}/{len(rows)} | total time normal {t_normal:.0f}s, fast {t_fast:.0f}s")


if __name__ == "__main__":
    main()
