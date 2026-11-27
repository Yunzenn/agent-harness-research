"""Score two detectors against task-level ground truth on real-model traces.

  A. novelty   : no new (world, result) signature for K steps   -> catches "frozen"
  B. progress  : task progress did not increase for K steps      -> catches "drifting"
                 (B needs a progress contract; A does not)

Ground truth: a trace is a failure if task progress < 1.0 when the loop ends.
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TRACES = os.path.join(HERE, "model_traces.json")
K = 4


def classify(trace: dict) -> str:
    calls = trace["trace"]
    if trace["done"]:
        return "completed"
    if not calls:
        return "no_action"
    # frozen: same world fingerprint for K consecutive calls
    worlds = [c["world"] for c in calls]
    for i in range(len(worlds) - K + 1):
        if len(set(worlds[i : i + K])) == 1:
            return "frozen"
    # drifting: no increase in task progress for K consecutive calls
    prog = [c["progress"] for c in calls]
    for i in range(len(prog) - K + 1):
        if prog[i + K - 1] <= prog[i]:
            return "drifting"
    return "premature_stop"


def detect_novelty(trace: dict, k: int = K) -> bool:
    calls = trace["trace"]
    seen = set()
    last_novel = -1
    for i, c in enumerate(calls):
        sig = (c["world"], c["result"])
        if sig not in seen:
            seen.add(sig)
            last_novel = i
        if i - last_novel >= k:
            return True
    return False


def detect_progress(trace: dict, k: int = K) -> bool:
    prog = [c["progress"] for c in trace["trace"]]
    for i in range(len(prog) - k + 1):
        if prog[i + k - 1] <= prog[i]:
            return True
    return False


def detect_contract(trace: dict, k: int = K) -> bool:
    """Needs a declared goal: `done_when` plus `advances`.

    - premature stop: the loop ended while progress < 1
    - drift: progress stagnant for K calls
    """
    if trace["done"]:
        return False
    if not trace["trace"]:
        return True
    if trace["progress"] < 1.0:
        return True
    return detect_progress(trace, k)


def score(rows: list[dict], fn) -> tuple[float, float]:
    tp = sum(1 for r in rows if r["failed"] and fn(r["raw"]))
    fp = sum(1 for r in rows if not r["failed"] and fn(r["raw"]))
    fn_ = sum(1 for r in rows if r["failed"] and not fn(r["raw"]))
    recall = tp / (tp + fn_) if tp + fn_ else 0.0
    precision = tp / (tp + fp) if tp + fp else 0.0
    return recall, precision


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else TRACES
    raw = json.load(open(path, encoding="utf-8"))
    rows = [{"kind": r["kind"], "failed": not r["done"], "mode": classify(r), "raw": r} for r in raw]

    print(f"traces={len(rows)}  K={K}")
    by_mode: dict[str, int] = {}
    for r in rows:
        by_mode[r["mode"]] = by_mode.get(r["mode"], 0) + 1
    for mode, n in sorted(by_mode.items(), key=lambda kv: -kv[1]):
        print(f"  {mode:14s} {n}")
    print()
    for name, fn in (("novelty", detect_novelty), ("progress", detect_progress), ("contract", detect_contract)):
        rec, prec = score(rows, fn)
        fired = sum(1 for r in rows if fn(r["raw"]))
        print(f"{name:9s} fired={fired:2d}  recall={rec:.0%}  precision={prec:.0%}")
    print()
    for r in rows:
        print(
            f"  {r['kind']:8s} mode={r['mode']:14s} done={r['raw']['done']!s:5s} "
            f"progress={r['raw']['progress']:.2f} novelty={detect_novelty(r['raw'])!s:5s} progress_det={detect_progress(r['raw'])}"
        )


if __name__ == "__main__":
    main()

