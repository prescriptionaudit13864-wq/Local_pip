"""Compare vLLM settings run on the same 20-doctor workload (T02.2 tune for speed).

Each file in benchmark/results/tuning/ is one `pilot_simulation run --scenario critical --seed 11` against a vLLM
started with different settings (start_vllm.ps1 switches). For every setting this prints and writes:
speed (per layer and for the medicine section), throughput, GPU load, KV cache size, prefix-cache reuse,
preemptions, errors caught (strict scoring) and how many section checks gave the same alerts as the baseline.

    python -m benchmark.vllm_tuning_compare --baseline A_current_bf16_0.88 --report docs/vllm_tuning_report.md
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

_args, sys.argv[1:] = sys.argv[1:], []
from benchmark.pilot_simulation import score  # noqa: E402

sys.argv[1:] = _args


def _pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * (len(xs) - 1)))] if xs else 0.0


def _alerts(res: dict) -> dict:
    """(doctor, prescription, section) -> (layer-1 rules, layer-2 kinds)."""
    out = {}
    for e in res["events"]:
        if "error" in e:
            continue
        out[(e["doctor"], e["rx"], e["section"])] = (
            tuple(sorted(a["rule"] for a in e.get("l1", []))),
            tuple(sorted(a["rule"] for a in e.get("l2", []))),
        )
    return out


def summarize(name: str, res: dict) -> dict:
    s = score(res)
    ok = [e for e in res["events"] if "error" not in e]
    both = [e["l1_s"] + e["l2_s"] for e in ok]
    rx = [e["l1_s"] + e["l2_s"] for e in ok if e["section"] == "rx"]
    m = res.get("metrics") or {}
    b, a = m.get("before") or {}, m.get("after") or {}
    q = (a.get("prefix_cache_queries_total", 0) - b.get("prefix_cache_queries_total", 0)) or 0
    h = (a.get("prefix_cache_hits_total", 0) - b.get("prefix_cache_hits_total", 0)) or 0
    blocks, bs = a.get("num_gpu_blocks"), a.get("block_size")
    g = res.get("gpu") or {}
    return {
        "name": name, "config": res.get("config", ""), "checks": len(ok), "failed": len(res["events"]) - len(ok),
        "per_min": round(len(ok) / res["wall_s"] * 60, 1), "wall_s": res["wall_s"],
        "l1_p50": round(statistics.median([e["l1_s"] for e in ok]), 2), "l1_p95": round(_pct([e["l1_s"] for e in ok], .95), 2),
        "l2_p50": round(statistics.median([e["l2_s"] for e in ok]), 2), "l2_p95": round(_pct([e["l2_s"] for e in ok], .95), 2),
        "both_p50": round(statistics.median(both), 2), "both_p95": round(_pct(both, .95), 2), "both_max": round(max(both), 1),
        "over6": sum(x > 6 for x in both), "over10": sum(x > 10 for x in both),
        "rx_p50": round(statistics.median(rx), 2) if rx else 0, "rx_p95": round(_pct(rx, .95), 2) if rx else 0,
        "caught": s["tot"]["either"], "errors": s["tot"]["errors"],
        "false": s["tot"]["false_l1"] + s["tot"]["false_l2"],
        "gpu_util": g.get("util_mean"), "power_mean": g.get("power_w_mean"), "power_max": g.get("power_w_max"),
        "mem_gb": round((g.get("mem_mib_max") or 0) / 1024, 1),
        "kv_tokens": (blocks * bs) if blocks and bs else None,
        "prefix_hit": round(h / q * 100, 1) if q else None,
        "preempt": round(a.get("num_preemptions_total", 0) - b.get("num_preemptions_total", 0)) if a else None,
        "types": s["types"],
    }


def agreement(base: dict, other: dict) -> dict:
    ab, ao = _alerts(base), _alerts(other)
    keys = sorted(set(ab) & set(ao))
    same1 = sum(ab[k][0] == ao[k][0] for k in keys)
    same2 = sum(ab[k][1] == ao[k][1] for k in keys)
    diffs = [(k, ab[k], ao[k]) for k in keys if ab[k] != ao[k]]
    return {"n": len(keys), "l1": same1, "l2": same2, "diffs": diffs}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", type=Path, default=Path("benchmark/results/tuning"))
    ap.add_argument("--baseline", default="A_current_bf16_0.88")
    ap.add_argument("--report", type=Path)
    a = ap.parse_args()
    runs = {p.stem: json.loads(p.read_text(encoding="utf-8-sig")) for p in sorted(a.dir.glob("*.json"))}
    rows = [summarize(n, r) for n, r in runs.items()]
    base = runs[a.baseline]
    for r in rows:
        ag = agreement(base, runs[r["name"]])
        r["agree_n"], r["agree_l1"], r["agree_l2"], r["diffs"] = ag["n"], ag["l1"], ag["l2"], ag["diffs"]
    print(json.dumps([{k: v for k, v in r.items() if k not in ("types", "diffs")} for r in rows], indent=1))
    if a.report:
        a.report.write_text(json.dumps(rows, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
