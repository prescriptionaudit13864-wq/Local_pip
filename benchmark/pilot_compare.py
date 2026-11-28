"""Compare two pilot-simulation runs of the same workload, with timestamps (e.g. tuned FP8 vs previous bf16).

    python -m benchmark.pilot_compare benchmark/results/pilot30/previous_bf16_0.88.json \\
        benchmark/results/pilot30/tuned_fp8.json --names Previous Tuned

Prints markdown tables: when each run started and ended, how long each doctor and each prescription took, the wait
per check (per layer, per section, by time band), checks finished per minute, errors caught, false alerts, how
many checks gave the same alerts, and GPU load.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

_args, sys.argv[1:] = sys.argv[1:], []
from benchmark.vllm_tuning_compare import agreement, summarize  # noqa: E402

sys.argv[1:] = _args


def _t(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def _pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * (len(xs) - 1)))] if xs else 0.0


def timing(res: dict) -> dict:
    ok = [e for e in res["events"] if "error" not in e]
    for e in ok:
        e["end"] = e["start"] + e.get("save_s", 0) + e["l1_s"] + e["l2_s"]
        e["wait"] = e["l1_s"] + e["l2_s"]
    start, end = min(e["start"] for e in ok), max(e["end"] for e in ok)
    docs, rxs = {}, {}
    for e in ok:
        docs.setdefault(e["doctor"], []).append(e)
        rxs.setdefault((e["doctor"], e["rx"]), []).append(e)
    per_doc = {d: {"first": min(x["start"] for x in v), "last": max(x["end"] for x in v),
                   "checks": len(v), "wait": sum(x["wait"] for x in v), "median": statistics.median(x["wait"] for x in v)}
               for d, v in docs.items()}
    per_rx = [{"key": k, "span": max(x["end"] for x in v) - min(x["start"] for x in v), "wait": sum(x["wait"] for x in v)}
              for k, v in rxs.items()]
    minute = {}
    for e in ok:
        m = int((e["end"] - start) // 60)
        minute[m] = minute.get(m, 0) + 1
    bands = [(0, 2), (2, 4), (4, 6), (6, 10), (10, 1e9)]
    return {"start": start, "end": end, "per_doc": per_doc, "per_rx": per_rx, "minute": minute,
            "bands": [sum(lo <= e["wait"] < hi for e in ok) for lo, hi in bands], "n": len(ok),
            "sections": {s: [e["wait"] for e in ok if e["section"] == s] for s in
                         ("chiefComplaints", "pastHistory", "investigations", "diagnosis", "rx", "advice")}}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("a", type=Path)
    ap.add_argument("b", type=Path)
    ap.add_argument("--names", nargs=2, default=["A", "B"])
    a = ap.parse_args()
    runs = [json.loads(p.read_text(encoding="utf-8-sig")) for p in (a.a, a.b)]
    na, nb = a.names
    sa, sb = summarize(na, runs[0]), summarize(nb, runs[1])
    ta, tb = timing(runs[0]), timing(runs[1])
    ag = agreement(runs[0], runs[1])
    f = lambda x: f"{x:.1f}"  # noqa: E731
    out = []
    out += ["## Run timestamps", "", f"| | {na} | {nb} |", "|---|---|---|",
            f"| Started | {_t(ta['start'])} | {_t(tb['start'])} |",
            f"| Last check finished | {_t(ta['end'])} | {_t(tb['end'])} |",
            f"| Total time | {f((ta['end'] - ta['start']) / 60)} min | {f((tb['end'] - tb['start']) / 60)} min |",
            f"| Section checks (failed) | {sa['checks']} ({sa['failed']}) | {sb['checks']} ({sb['failed']}) |",
            f"| Checks per minute | {sa['per_min']} | {sb['per_min']} |", ""]
    out += ["## Wait per section check (both layers)", "", f"| | {na} | {nb} | Change |", "|---|---|---|---|"]
    for label, k in (("Median", "both_p50"), ("95% within", "both_p95"), ("Slowest", "both_max"),
                     ("Layer 1 median", "l1_p50"), ("Layer 1 95%", "l1_p95"), ("Layer 2 median", "l2_p50"),
                     ("Layer 2 95%", "l2_p95"), ("Medicine section median", "rx_p50"), ("Medicine section 95%", "rx_p95")):
        x, y = sa[k], sb[k]
        out.append(f"| {label} | {x} s | {y} s | {'−' if y < x else '+'}{abs(round((y - x) / x * 100)) if x else 0}% |")
    out += ["", "### Wait by time band", "", f"| Wait | {na} | {nb} |", "|---|---|---|"]
    for label, i in (("under 2 s", 0), ("2–4 s", 1), ("4–6 s", 2), ("6–10 s", 3), ("over 10 s", 4)):
        out.append(f"| {label} | {ta['bands'][i]} ({round(ta['bands'][i] / ta['n'] * 100)}%) | "
                   f"{tb['bands'][i]} ({round(tb['bands'][i] / tb['n'] * 100)}%) |")
    out += ["", "### By section (median / 95% within)", "", f"| Section | {na} | {nb} |", "|---|---|---|"]
    for s in ta["sections"]:
        xa, xb = ta["sections"][s], tb["sections"][s]
        if xa and xb:
            out.append(f"| {s} | {f(statistics.median(xa))} / {f(_pct(xa, .95))} s | {f(statistics.median(xb))} / {f(_pct(xb, .95))} s |")
    out += ["", "## Per doctor (5 prescriptions each)", "",
            f"| Doctor | {na}: first → last check | {na}: time to finish | {na}: median wait | "
            f"{nb}: first → last check | {nb}: time to finish | {nb}: median wait |", "|---|---|---|---|---|---|---|"]
    for d in sorted(ta["per_doc"]):
        x, y = ta["per_doc"][d], tb["per_doc"].get(d)
        if not y:
            continue
        out.append(f"| {d} | {_t(x['first'])} → {_t(x['last'])} | {f((x['last'] - x['first']) / 60)} min | {f(x['median'])} s | "
                   f"{_t(y['first'])} → {_t(y['last'])} | {f((y['last'] - y['first']) / 60)} min | {f(y['median'])} s |")
    fin_a = [(v["last"] - v["first"]) / 60 for v in ta["per_doc"].values()]
    fin_b = [(v["last"] - v["first"]) / 60 for v in tb["per_doc"].values()]
    out += [f"| **Median doctor** | | **{f(statistics.median(fin_a))} min** | | | **{f(statistics.median(fin_b))} min** | |", ""]
    out += ["## Per prescription", "", f"| | {na} | {nb} |", "|---|---|---|"]
    for label, key in (("Time waiting for checks (sum per prescription), median", "wait"),
                       ("Start to end of a prescription (incl. writing pauses), median", "span")):
        xa = [r[key] for r in ta["per_rx"]]
        xb = [r[key] for r in tb["per_rx"]]
        out.append(f"| {label} | {f(statistics.median(xa))} s (95%: {f(_pct(xa, .95))} s) | {f(statistics.median(xb))} s (95%: {f(_pct(xb, .95))} s) |")
    out += ["", "## Checks finished per minute", "", f"| Minute | {na} | {nb} |", "|---|---|---|"]
    for m in range(max(max(ta["minute"]), max(tb["minute"])) + 1):
        out.append(f"| {m + 1} | {ta['minute'].get(m, 0)} | {tb['minute'].get(m, 0)} |")
    out += ["", "## Accuracy and answers", "", f"| | {na} | {nb} |", "|---|---|---|",
            f"| Errors caught (strict) | {sa['caught']} / {sa['errors']} | {sb['caught']} / {sb['errors']} |",
            f"| Alerts on error-free lines | {sa['false']} | {sb['false']} |",
            f"| Layer 1 alerts same as {na} | — | {ag['l1']} / {ag['n']} ({round(ag['l1'] / ag['n'] * 100, 1)}%) |",
            f"| Layer 2 alerts same as {na} | — | {ag['l2']} / {ag['n']} ({round(ag['l2'] / ag['n'] * 100, 1)}%) |", ""]
    out += ["### Errors caught by type", "", f"| Type | {na} | {nb} |", "|---|---|---|"]
    for ty in sorted(sa["types"], key=lambda k: -sa["types"][k]["n"]):
        x, y = sa["types"][ty], sb["types"].get(ty, {"either": 0, "n": 0})
        out.append(f"| {ty} | {x['either']} / {x['n']} | {y['either']} / {y['n']} |")
    out += ["", "## GPU and memory", "", f"| | {na} | {nb} |", "|---|---|---|",
            f"| GPU busy (mean) | {sa['gpu_util']}% | {sb['gpu_util']}% |",
            f"| Power mean / peak | {sa['power_mean']} / {sa['power_max']} W | {sb['power_mean']} / {sb['power_max']} W |",
            f"| Energy | {f(sa['power_mean'] * runs[0]['wall_s'] / 3600)} Wh | {f(sb['power_mean'] * runs[1]['wall_s'] / 3600)} Wh |",
            f"| Prefix-cache reuse | {sa['prefix_hit']}% | {sb['prefix_hit']}% |",
            f"| Requests pushed out (preemptions) | {sa['preempt']} | {sb['preempt']} |", ""]
    print("\n".join(out))


if __name__ == "__main__":
    main()
