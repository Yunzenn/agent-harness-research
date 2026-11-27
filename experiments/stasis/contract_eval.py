"""Replay the real-model traces through ContractMonitor.

Answers two questions:
  1. does a contract catch every failure the novelty detector missed?
  2. how many tool calls would it have saved by stopping early?
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_KIT = os.path.join(os.path.dirname(os.path.dirname(HERE)), "harness-research", "loopkit")
sys.path.insert(0, REPO_KIT)

from loopkit.contract import Contract, ContractMonitor  # noqa: E402


def contract_for(kind: str) -> Contract:
    if kind == "tickets":
        return Contract(
            name="resolve tickets",
            done_when=lambda p: p >= 1.0,
            advances=lambda p: p,
            stall_steps=4,
        )
    if kind == "batch":
        return Contract(name="summarise docs", done_when=lambda p: p >= 1.0, advances=lambda p: p, stall_steps=4)
    return Contract(name="answer questions", done_when=lambda p: p >= 1.0, advances=lambda p: p, stall_steps=4)


def replay(trace: dict) -> dict:
    mon = ContractMonitor(contract_for(trace["kind"]))
    caught_at = None
    caught_kind = None
    for i, call in enumerate(trace["trace"]):
        v = mon.observe(value=call["progress"])
        if v and caught_at is None:
            caught_at = i + 1
            caught_kind = v.kind
    end = mon.before_stop(done=trace["done"])
    if end and caught_at is None:
        caught_kind = end.kind
        caught_at = len(trace["trace"])
    return {
        "kind": trace["kind"],
        "done": trace["done"],
        "calls": len(trace["trace"]),
        "caught_kind": caught_kind,
        "caught_at_call": caught_at,
        "calls_saved": (len(trace["trace"]) - caught_at) if caught_at else 0,
    }


def main() -> None:
    path = os.path.join(HERE, sys.argv[1] if len(sys.argv) > 1 else "model_traces.json")
    traces = json.load(open(path, encoding="utf-8"))
    rows = [replay(t) for t in traces]

    print(f"traces={len(rows)}")
    failed = [r for r in rows if not r["done"]]
    caught = [r for r in failed if r["caught_kind"]]
    print(f"failed={len(failed)}  caught={len(caught)}  recall={len(caught)/len(failed):.0%}")
    false_flags = [r for r in rows if r["done"] and r["caught_kind"]]
    print(f"healthy={len(rows)-len(failed)}  false_flags={len(false_flags)}")
    saved = sum(r["calls_saved"] for r in rows)
    total = sum(r["calls"] for r in rows)
    print(f"calls saved by early stop: {saved}/{total} ({saved/total:.0%})")
    print()
    for r in rows:
        print(
            f"  {r['kind']:8s} done={r['done']!s:5s} calls={r['calls']:2d} "
            f"caught={r['caught_kind'] or '-':14s} at={r['caught_at_call'] or '-':>3} saved={r['calls_saved']}"
        )


if __name__ == "__main__":
    main()

