"""Run the stasis experiment.

    python -m experiments.stasis.run          # from D:\\pai0.7
    python experiments/stasis/run.py

Measures, per workload and per threshold K:
  - detection rate on stalled loops (want high)
  - detection lag in steps after progress actually stopped (want low)
  - false positives on healthy loops (want zero)
"""

from __future__ import annotations

import json
import os
import sys
import statistics

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from experiments.stasis.monitor import StasisMonitor  # noqa: E402
from experiments.stasis.scenarios import BUILDERS  # noqa: E402
from experiments.stasis.world import Tools, make_world  # noqa: E402

SEEDS = 20
KS = [3, 4, 5, 6]


def run_one(builder, seed: int, k: int) -> dict:
    sc = builder(seed)
    w = make_world()
    tools = Tools(w)
    mon = StasisMonitor(k=k)
    for step, (name, args) in enumerate(sc.actions[: sc.max_steps]):
        result = tools.call(name, args)
        mon.observe(step, w.state(), result)
    return {
        "scenario": sc.name,
        "onset": sc.onset,
        "detected_at": mon.detected_at,
        "steps": min(len(sc.actions), sc.max_steps),
    }


def main() -> None:
    rows = []
    for (name, kind), builder in BUILDERS.items():
        for k in KS:
            runs = [run_one(builder, seed, k) for seed in range(SEEDS)]
            det = [r for r in runs if r["detected_at"] is not None]
            rate = len(det) / len(runs)
            lag = statistics.median([r["detected_at"] - r["onset"] for r in det]) if det and kind == "stall" else None
            rows.append({"scenario": name, "kind": kind, "k": k, "rate": rate, "lag": lag, "n": len(runs)})
            if kind == "stall":
                print(f"{name:12s} stall   K={k}  detected={rate:5.0%}  median_lag={lag if lag is not None else '-'} steps")
            else:
                print(f"{name:12s} healthy K={k}  false_positive={rate:5.0%}")

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=1)
    print("\nwrote", out)


if __name__ == "__main__":
    main()
