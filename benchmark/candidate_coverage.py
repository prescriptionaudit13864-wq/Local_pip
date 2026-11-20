"""How many physician-labelled errors can the live pipeline report at all?

In fine-tune mode the model only confirms candidates the rule engine raises: a rule the model flags with no
matching candidate in that section is dropped (finetune_prompt.findings_from_detected). This runs every gold
prescription through the real pipeline (audit_prescription) with a recording stand-in provider that returns no
findings, and counts, per rule, how many physician labels had a matching candidate. No GPU is used.

    python -m benchmark.candidate_coverage [--live] [path to LLM_Trainer/data]

--live uses the live section-by-section mode (live_section_audit=True), which adds rulebook-review candidates
for rules the engines never raise; without it, the final-save mode. Writes benchmark/results/
candidate_coverage[_live].json (per-label detail) and prints a per-rule table.
"""

from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

from audit_pipeline.finetune_prompt import RULE_ALIASES, flatten_candidates, parse_section_task
from audit_pipeline.orchestrator import audit_prescription

LIVE = "--live" in sys.argv  # live section-by-section mode, which adds rulebook-review candidates
_ARGS = [a for a in sys.argv[1:] if a != "--live"]
DATA = Path(_ARGS[0] if _ARGS else r"C:\Users\HEAT\Documents\LLM_Trainer\data")
OUT = Path(__file__).resolve().parent / "results" / f"candidate_coverage{'_live' if LIVE else ''}.json"
FIELDS = (
    "Age",
    "Gender",
    "Chief_Complaint",
    "Health_Screening",
    "On_Examination",
    "Systemic_Examination",
    "Investigation",
    "Investigation_Results",
    "Diagnosis",
    "Medication",
    "Advice",
    "ANC_Notes",
)


class Recorder:
    """Stands in for the model: keeps each section task's candidates and confirms nothing."""

    def __init__(self) -> None:
        self.seen: dict[str, set[str]] = collections.defaultdict(set)

    def audit(self, *, system: str, prompt: str, max_tokens: int):
        try:
            task = parse_section_task(prompt)
        except Exception:
            return {"findings": []}
        section = str(task.get("section", ""))
        for c in flatten_candidates(task.get("candidates")):
            rule = str(c.get("rule_id", "")).strip()
            for trained in RULE_ALIASES.get(rule, (rule,)):
                self.seen[section].add(trained)
        return {"section": section, "findings": []}


def load(split: str) -> list[dict]:
    with open(DATA / f"gold_v5_best_section_{split}.jsonl", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def main() -> None:
    labels: dict[str, list[tuple[str, str, str]]] = collections.defaultdict(list)
    context: dict[str, dict] = {}
    split_of: dict[str, str] = {}
    for split in ("train", "validation", "test"):
        for row in load(split):
            pid = row["prescription_id"]
            split_of[pid] = split
            context.setdefault(pid, {}).update({k: v for k, v in row["input"]["prescription_context"].items() if v})
            for e in row["output"]["detected_errors"]:
                labels[pid].append((row["target_section"], e["rule_id"], split))

    records, failures = [], []
    for i, pid in enumerate(sorted(context), 1):
        rec = Recorder()
        prescription = {"Prescription_ID": pid, **{k: context[pid].get(k, "") for k in FIELDS}}
        try:
            audit_prescription(prescription, {"live_section_audit": LIVE}, provider=rec)
        except Exception as exc:  # keep going; a crash means no candidates for that prescription
            failures.append((pid, f"{type(exc).__name__}: {exc}"[:200]))
        for section, rule, split in labels.get(pid, []):
            records.append(
                {
                    "id": pid,
                    "split": split,
                    "section": section,
                    "rule": rule,
                    "covered": rule in rec.seen.get(section, set()),
                    "candidates_in_section": sorted(rec.seen.get(section, set())),
                }
            )
        if i % 25 == 0:
            print(f"  {i}/{len(context)} prescriptions", flush=True)

    per = collections.defaultdict(lambda: [0, 0])
    for r in records:
        per[r["rule"]][1] += 1
        per[r["rule"]][0] += r["covered"]
    total = sum(v[1] for v in per.values())
    covered = sum(v[0] for v in per.values())
    print(
        f"\n{len(context)} prescriptions, {total} physician labels, {covered} with a matching candidate "
        f"({100 * covered / max(total, 1):.0f}%)"
    )
    print(f"{'rule':12} {'covered':>8} {'labels':>7}")
    for rule, (c, n) in sorted(per.items(), key=lambda kv: -kv[1][1]):
        print(f"{rule:12} {c:>8} {n:>7}   {100 * c / n:>4.0f}%")
    if failures:
        print(f"\n{len(failures)} prescriptions failed in the pipeline, e.g. {failures[:3]}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps({"records": records, "failures": failures}, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print("wrote", OUT)


if __name__ == "__main__":
    main()
