"""Engine audit: 10 synthetic prescriptions with a known answer, run on the current engine and on vLLM.

Each case is written section by section the way the app does (layer 1 fast check, then layer 2 clinical reviewer).
Every case lists the lines that hold a real error; a clean case lists none. Scoring is by line, so it does not depend
on which rule name an engine chose:
- caught  = an expected error line got at least one alert (layer 1, layer 2, or either for "system")
- missed  = an expected error line got no alert
- false   = an alert on a line that holds no error
While the cases run, the GPU is sampled every 0.5 s (utilisation, memory, power). No patient data.

    # app running (.\\run_local.ps1) on the engine under test
    python -m benchmark.engine_audit run --out benchmark/results/engine_audit_vllm.json
    python -m benchmark.engine_audit run --out benchmark/results/engine_audit_local.json
    python -m benchmark.engine_audit report benchmark/results/engine_audit_local.json benchmark/results/engine_audit_vllm.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import threading
import time
from datetime import date
from pathlib import Path

_args, sys.argv[1:] = sys.argv[1:], []
from benchmark.live_suite import ORDER, med  # noqa: E402
from benchmark.vllm_equivalence import _post  # noqa: E402

sys.argv[1:] = _args

# (id, title, sex, age, entries by record key, expected error lines [(section, text in the line)])
CASES = [
    ("S01", "Male with a menstrual complaint", "Male", 34,
     {"chiefComplaints": ["Irregular menstruation for 2 months", "Back pain for 1 week"]},
     [("chiefComplaints", "irregular menstruation")]),
    ("S02", "Complaint with no duration", "Female", 28,
     {"chiefComplaints": ["Sore throat for 3 days", "Dry cough"]},
     [("chiefComplaints", "dry cough")]),
    ("S03", "Illness longer than the patient's life", "Female", 19,
     {"chiefComplaints": ["Fatigue for 1 week"], "pastHistory": ["Hypertension for 25 years"]},
     [("pastHistory", "hypertension for 25 years")]),
    ("S04", "Prostate test for a woman", "Female", 52,
     {"chiefComplaints": ["Burning urination for 4 days"], "investigations": ["Urine R/M/E", "Serum PSA"]},
     [("investigations", "psa")]),
    ("S05", "Pregnancy diagnosis in a man", "Male", 40,
     {"chiefComplaints": ["Abdominal pain for 2 days"], "diagnosis": ["Ectopic pregnancy"]},
     [("diagnosis", "ectopic pregnancy")]),
    ("S06", "Adult tablet dose for a 3-year-old", "Male", 3,
     {"chiefComplaints": ["Fever for 2 days"], "diagnosis": ["Viral fever"],
      "rxList": [med("Napa", "Paracetamol", "500 mg", "1+1+1", "3 days")]},
     [("rx", "napa")]),
    ("S07", "Clean adult hypertension visit", "Male", 58,
     {"chiefComplaints": ["Headache for 5 days"], "pastHistory": ["Hypertension for 6 years"],
      "diagnosis": ["Essential hypertension"],
      "rxList": [med("Amdocal", "Amlodipine", "5 mg", "0+0+1", "30 days")],
      "adviceList": ["Reduce salt intake", "Check blood pressure weekly"]},
     []),
    ("S08", "Clean child diarrhoea visit", "Female", 6,
     {"chiefComplaints": ["Loose motion for 2 days"], "diagnosis": ["Acute watery diarrhoea"],
      "rxList": [med("Orsaline-N", "Oral rehydration salts", "", "1 sachet after each loose stool", "3 days",
                     form="Powder")],
      "adviceList": ["Give plenty of fluids", "Continue feeding"]},
     []),
    ("S09", "Clean adult cough visit", "Female", 35,
     {"chiefComplaints": ["Cough for 4 days", "Fever for 2 days"], "diagnosis": ["Acute bronchitis"],
      "investigations": ["CBC"],
      "rxList": [med("Napa", "Paracetamol", "500 mg", "1+1+1", "3 days")],
      "adviceList": ["Drink warm water"]},
     []),
    ("S10", "Two errors in one visit", "Male", 30,
     {"chiefComplaints": ["Vaginal discharge for 5 days", "Joint pain"]},
     [("chiefComplaints", "vaginal discharge"), ("chiefComplaints", "joint pain")]),
]


class GpuSampler(threading.Thread):
    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.rows: list[tuple[float, float, float]] = []
        self.stop = threading.Event()

    def run(self) -> None:
        q = ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,power.draw", "--format=csv,noheader,nounits"]
        while not self.stop.is_set():
            try:
                u, m, p = (float(x) for x in subprocess.check_output(q, text=True).strip().split(","))
                self.rows.append((u, m, p))
            except Exception:  # noqa: BLE001 - a missed sample is fine
                pass
            self.stop.wait(0.5)

    def summary(self) -> dict:
        if not self.rows:
            return {}
        u, m, p = zip(*self.rows)
        return {"samples": len(u), "util_mean": round(statistics.mean(u), 1), "util_max": max(u),
                "mem_mib_max": max(m), "power_w_mean": round(statistics.mean(p), 1), "power_w_max": max(p)}


def run(base: str) -> dict:
    health = json.loads(_post_get(base + "/health"))
    gpu = GpuSampler()
    gpu.start()
    started = time.perf_counter()
    out = []
    for n, (cid, title, sex, age, entries, expected) in enumerate(CASES, 1):
        patient = {"id": 993000 + n, "serial": cid, "name": f"Synthetic {cid}", "gender": sex,
                   "dob": date(date.today().year - age, 1, 1).isoformat(), "ageValue": age, "ageUnit": "years"}
        visit: dict = {"id": 1799900000000 + n}
        checks = []
        for section, key in ORDER:
            if key not in entries:
                continue
            v = entries[key]
            visit[key] = v if key == "rxList" else [{"id": i + 1, "text": t} for i, t in enumerate(v)]
            payload = {"patient": patient, "visit": dict(visit), "section": section, "revision": 1}
            body, s1 = _post(base, "section", payload)
            live = body.get("live_audit", {})
            l1 = [{"rule": str(a.get("rule_id", "")), "evidence": str(a.get("evidence", "")), "why": str(a.get("why", ""))}
                  for a in (live.get("findings") or []) if isinstance(a, dict)]
            review, s2 = _post(base, "review", {**payload, "layer1": [a["why"] for a in l1]})
            l2 = [{"type": str(p.get("type")), "evidence": str(p.get("entry")), "why": str(p.get("problem"))}
                  for p in review.get("review", {}).get("problems", [])]
            checks.append({"section": section, "l1": l1, "l2": l2, "l1_s": round(s1, 2), "l2_s": round(s2, 2)})
        out.append({"id": cid, "title": title, "expected": expected, "checks": checks})
    wall = time.perf_counter() - started
    gpu.stop.set()
    gpu.join()
    return {"engine": health.get("provider"), "model": health.get("model"), "wall_s": round(wall, 1),
            "gpu": gpu.summary(), "cases": out}


def _post_get(url: str) -> str:
    import urllib.request
    return urllib.request.urlopen(url, timeout=60).read().decode()


def score(res: dict) -> dict:
    tot = {k: {"caught": 0, "missed": 0, "false": 0} for k in ("l1", "l2", "system")}
    per_case = []
    for c in res["cases"]:
        exp = [(s, t.casefold()) for s, t in c["expected"]]
        row = {"id": c["id"], "title": c["title"]}
        for layer in ("l1", "l2", "system"):
            alerts = [(ch["section"], (a["evidence"] + " " + a["why"]).casefold())
                      for ch in c["checks"] for lay in (("l1", "l2") if layer == "system" else (layer,))
                      for a in ch[lay]]
            caught = sum(1 for s, t in exp if any(s == sa and t in ea for sa, ea in alerts))
            # one false alert per wrong line, so the two layers saying the same wrong thing count once
            false_lines = {(sa, ea) for sa, ea in alerts if not any(s == sa and t in ea for s, t in exp)}
            tot[layer]["caught"] += caught
            tot[layer]["missed"] += len(exp) - caught
            tot[layer]["false"] += len(false_lines)
            row[layer] = f"{caught}/{len(exp)}" + (f", {len(false_lines)} false" if false_lines else "")
        row["ok"] = row["system"] == f"{len(exp)}/{len(exp)}"
        per_case.append(row)
    l1 = [ch["l1_s"] for c in res["cases"] for ch in c["checks"]]
    l2 = [ch["l2_s"] for c in res["cases"] for ch in c["checks"]]
    return {"tot": tot, "cases": per_case, "l1_med": statistics.median(l1), "l1_max": max(l1),
            "l2_med": statistics.median(l2), "l2_max": max(l2), "checks": len(l1)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--server", default="http://127.0.0.1:8765")
    r.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("report")
    p.add_argument("files", type=Path, nargs="+")
    a = ap.parse_args()
    if a.cmd == "run":
        res = run(a.server.rstrip("/"))
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({"engine": res["engine"], "wall_s": res["wall_s"], "gpu": res["gpu"]}))
        print(json.dumps(score(res), indent=1))
    else:
        for f in a.files:
            res = json.loads(f.read_text(encoding="utf-8"))
            print(f, res["engine"], res["wall_s"], res["gpu"])
            print(json.dumps(score(res), indent=1))


if __name__ == "__main__":
    main()
