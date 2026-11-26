"""Command line: `python -m loopkit ...`

    advise   描述业务 -> 打印推荐拓扑、预算、闸门与理由
    demo     跑三个最小工作流，打印轨迹与耗时
    bench    调用 bench/bench.py
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

from .advisor import Brief, advise
from .kernel import Harness
from .llm import MockProvider, Tiered
from .patterns import Lane, fanout, react, route
from .tools import Param, Registry, Tool
from .types import Action, Budget, Stop, ToolCall


def _plan(args) -> int:
    brief = Brief(
        goal=args.goal,
        oracle=args.oracle,
        reversible=args.blast != "irreversible",
        blast_radius=args.blast,
        lifetime=args.lifetime,
        heterogeneity=args.heterogeneity,
        subtasks_known=not args.unknown_subtasks,
        fanout=args.fanout,
        latency_budget_s=args.latency,
        cost_budget_usd=args.cost,
    )
    plan = advise(brief)
    print(plan.explain())
    return 0


def _demo(_args) -> int:
    # -- a tiny world ------------------------------------------------------
    reg = Registry()
    state = {"tests": 0}

    reg.register(Tool("read", "Read a file.", {"path": Param("string")}, lambda path: f"source of {path}"))
    reg.register(Tool("run_tests", "Run tests.", {}, lambda: (state.__setitem__("tests", state["tests"] + 1), "2 passed")[1], cache_ttl_s=30))
    reg.register(Tool("refund", "Send money.", {"amount": Param("integer")}, lambda amount: f"refunded {amount}", read_only=False, idempotent=False, parallel_safe=False, destructive=True))

    # -- 1. react with an external judge ----------------------------------
    def bug_brain(req):
        n = sum(1 for m in req.messages if m.get("role") == "assistant")
        if n == 0:
            return Action(calls=[ToolCall("read", {"path": "calc.py"})])
        if n == 1:
            return Action(calls=[ToolCall("run_tests", {})])
        return Action(text="fixed")

    h = Harness(MockProvider(bug_brain, latency_s=0.01), reg, Budget(max_steps=6))
    h.log.append("user", text="fix add()")
    res = react(h, lambda hh: "tests_pass" if state["tests"] else None)
    print(f"\n[1] react (L3)          {res.line()}")
    print("    " + h.log.transcript().replace("\n", "\n    "))

    # -- 2. route with a narrow lane --------------------------------------
    def classify(hh):
        return "billing" if "invoice" in hh.log.last_text() else "escalate"

    def ticket_brain(req):
        return Action(text="billing: duplicate charge found")

    h2 = Harness(MockProvider(ticket_brain, latency_s=0.01), reg, Budget(max_steps=4))
    h2.log.append("user", text="my invoice was charged twice")
    res2 = route(h2, classify, {"billing": Lane("billing", ["read"], lambda hh: "ok" if hh.log.last_text() else None)})
    print(f"\n[2] route (L2)          {res2.line()}  lane={res2.metrics.get('lane')}")

    # -- 3. fan-out with isolated contexts --------------------------------
    def worker(req):
        n = sum(1 for m in req.messages if m.get("role") == "assistant")
        if n == 0:
            doc = req.messages[0]["content"].split()[-1]
            return Action(calls=[ToolCall("read", {"path": doc})])
        return Action(text="claim " + req.messages[0]["content"].split()[-1])

    parent = Harness(MockProvider(lambda r: Action(text="")), reg, Budget(max_steps=2))
    parent.log.append("user", text="read 6 docs")
    res3 = fanout(
        parent,
        [f"d{i}" for i in range(6)],
        spawn=lambda item: Harness(MockProvider(worker, latency_s=0.01), reg, Budget(max_steps=3)),
        task=lambda item: f"read {item}",
        done=lambda hh, item: "ok" if hh.log.last_text().startswith("claim") else None,
        merge=lambda outs: "; ".join(o.text for o in outs),
        max_workers=3,
    )
    print(f"\n[3] fanout (L5)         {res3.line()}  workers={res3.metrics['workers']}")

    # -- 4. tiered models --------------------------------------------------
    cheap = MockProvider(lambda r: Action(text="billing"), latency_s=0.005, name="small")
    strong = MockProvider(lambda r: Action(text="billing"), latency_s=0.03, name="large")
    tiered = Tiered({"router": cheap, "exec": strong})
    print(f"\n[4] tiered routing      model=small->{cheap.name}, model=large->{strong.name}, provider={tiered.name}")
    print("\n同一个 kernel，四种控制流。差异在控制器，不在循环。")
    return 0


def _bench(_args) -> int:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return subprocess.call([sys.executable, os.path.join(root, "bench", "bench.py")])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="loopkit", description="场景自适应 + 加速的最小 agent harness")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("advise", help="业务描述 -> 拓扑与预算")
    p.add_argument("--goal", default="")
    p.add_argument("--oracle", default="none", choices=["code", "render", "schema", "rubric", "human", "none"])
    p.add_argument("--blast", default="read", choices=["read", "write", "irreversible"])
    p.add_argument("--lifetime", default="session", choices=["session", "hours", "days"])
    p.add_argument("--heterogeneity", default="single", choices=["single", "few", "many"])
    p.add_argument("--fanout", type=int, default=1)
    p.add_argument("--latency", type=float, default=60.0)
    p.add_argument("--cost", type=float, default=1.0)
    p.add_argument("--unknown-subtasks", action="store_true")
    p.set_defaults(fn=_plan)

    d = sub.add_parser("demo", help="跑四个最小工作流")
    d.set_defaults(fn=_demo)

    b = sub.add_parser("bench", help="跑基准")
    b.set_defaults(fn=_bench)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
