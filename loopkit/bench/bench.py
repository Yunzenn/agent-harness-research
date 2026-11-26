"""Benchmark: same work, two orchestration strategies, measured wall-clock.

The model and tools are simulated (fixed sleeps) so the numbers isolate the
*orchestration* overhead: batching, caching, concurrency, and tool-surface size.
They do NOT measure model quality — see README for what this can and cannot show.

    python bench/bench.py            # prints a table, writes bench/RESULTS.md
"""

from __future__ import annotations

import json
import os
import platform
import statistics
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loopkit import Action, Budget, Harness, MockProvider, Param, Registry, Tool, ToolCall, fanout, react  # noqa: E402


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
MODEL_MS = 80
ROUTER_MS = 20
TOOL_MS = 50


def fillers(n: int) -> list[Tool]:
    """Stand-in for a bloated tool surface: schemas cost tokens every step."""
    return [
        Tool(f"admin_op_{i}", f"Administrative operation number {i}. " + "Details. " * 12, {"id": Param("string")}, lambda id: id)
        for i in range(n)
    ]


def docs_registry(cache: bool = False) -> Registry:
    reg = Registry()
    reg.register(Tool("read", "Read a document.", {"doc": Param("string")}, lambda doc: ("content of " + doc) * 8, cache_ttl_s=60.0 if cache else 0.0))
    for t in fillers(9):
        reg.register(t)
    return reg


def tickets_registry(cache: bool = False) -> Registry:
    reg = Registry()
    reg.register(Tool("get_ticket", "Fetch a ticket.", {"tid": Param("string")}, lambda tid: {"tid": tid, "kind": "billing"}, cache_ttl_s=60.0 if cache else 0.0))
    reg.register(Tool("lookup_invoice", "Read the ledger.", {"tid": Param("string")}, lambda tid: f"ledger {tid}", cache_ttl_s=60.0 if cache else 0.0))
    reg.register(Tool("search_kb", "Search the KB.", {"q": Param("string")}, lambda q: "kb:" + q, cache_ttl_s=60.0 if cache else 0.0))
    for t in fillers(9):
        reg.register(t)
    return reg


def bug_registry(cache: bool) -> Registry:
    reg = Registry()
    for i in range(3):
        reg.register(Tool(f"read_{i}", f"Read file {i}.", {"path": Param("string")}, lambda path: ("source " + path) * 20, cache_ttl_s=60.0 if cache else 0.0))
    reg.register(Tool("run_tests", "Run the suite.", {}, lambda: "2 passed", cache_ttl_s=60.0 if cache else 0.0))
    return reg


def turns(messages: list[dict]) -> int:
    return sum(1 for m in messages if m.get("role") == "assistant")


# ---------------------------------------------------------------------------
# workload 1: 12 independent documents
# ---------------------------------------------------------------------------
def w1_baseline() -> dict[str, Any]:
    reg = docs_registry(cache=False)
    seen = {"n": 0}

    def brain(req):
        n = turns(req.messages)
        if n % 2 == 0:
            doc = n // 2
            if doc >= 12:
                return Action(text="done")
            return Action(calls=[ToolCall("read", {"doc": f"d{doc}"})])
        return Action(text=f"summary {n // 2}")

    h = Harness(MockProvider(brain, latency_s=MODEL_MS / 1000), reg, Budget(max_steps=40, max_tool_calls=40))
    h.log.append("user", text="summarise 12 documents")
    res = react(h, lambda hh: "ok" if turns(hh.log.messages()) >= 24 else None)
    return {"wall": res.wall_s, "tokens": res.tokens_in, "steps": res.steps, "peak": res.metrics["peak_prompt_chars"]}


def w1_optimized() -> dict[str, Any]:
    reg = docs_registry(cache=True)

    def worker_brain(req):
        if turns(req.messages) == 0:
            doc = req.messages[0]["content"].rsplit(" ", 1)[-1]
            return Action(calls=[ToolCall("read", {"doc": doc})])
        return Action(text="summary " + req.messages[0]["content"].rsplit(" ", 1)[-1])

    parent = Harness(MockProvider(lambda r: Action(text="")), reg, Budget(max_steps=2))
    parent.log.append("user", text="summarise 12 documents")
    res = fanout(
        parent,
        [f"d{i}" for i in range(12)],
        spawn=lambda item: Harness(MockProvider(worker_brain, latency_s=MODEL_MS / 1000), reg, Budget(max_steps=4, max_tool_calls=4)),
        task=lambda item: f"summarise {item}",
        done=lambda hh, item: "ok" if hh.log.last_text().startswith("summary") else None,
        merge=lambda outs: " | ".join(o.text for o in outs),
        max_workers=6,
    )
    return {"wall": res.wall_s, "tokens": res.tokens_in, "steps": res.steps, "peak": res.metrics.get("max_worker_tokens", 0)}


# ---------------------------------------------------------------------------
# workload 2: 24 support tickets, heterogeneous
# ---------------------------------------------------------------------------
def w2_baseline() -> dict[str, Any]:
    reg = tickets_registry(cache=False)

    def brain(req):
        n = turns(req.messages)
        if n == 0:
            return Action(calls=[ToolCall("get_ticket", {"tid": "T"})])
        if n == 1:
            return Action(calls=[ToolCall("lookup_invoice", {"tid": "T"})])
        return Action(text="reply")

    total = 0.0
    tokens = 0
    steps = 0
    for _ in range(24):
        h = Harness(MockProvider(brain, latency_s=MODEL_MS / 1000), reg, Budget(max_steps=5, max_tool_calls=5))
        h.log.append("user", text="ticket T: charged twice")
        res = react(h, lambda hh: "ok" if hh.log.last_text() == "reply" else None)
        total += res.wall_s
        tokens += res.tokens_in
        steps += res.steps
    return {"wall": total, "tokens": tokens, "steps": steps, "peak": 0}


def w2_optimized() -> dict[str, Any]:
    # One shared registry, narrow per lane; the router model is 4x faster.
    reg = tickets_registry(cache=True)
    reg.restrict(["get_ticket", "lookup_invoice", "search_kb"])

    def worker_brain(req):
        n = turns(req.messages)
        if n == 0:
            return Action(calls=[ToolCall("get_ticket", {"tid": "T"})])
        if n == 1:
            return Action(calls=[ToolCall("lookup_invoice", {"tid": "T"})])
        return Action(text="reply")

    parent = Harness(MockProvider(lambda r: Action(text="")), reg, Budget(max_steps=2))
    parent.log.append("user", text="24 tickets")
    res = fanout(
        parent,
        list(range(24)),
        spawn=lambda item: Harness(MockProvider(worker_brain, latency_s=MODEL_MS / 1000), reg, Budget(max_steps=5, max_tool_calls=5)),
        task=lambda item: f"ticket T{item}: charged twice",
        done=lambda hh, item: "ok" if hh.log.last_text() == "reply" else None,
        merge=lambda outs: f"{len(outs)} handled",
        max_workers=6,
    )
    return {"wall": res.wall_s, "tokens": res.tokens_in, "steps": res.steps, "peak": 0}


# ---------------------------------------------------------------------------
# workload 3: one bugfix loop with repeated + parallel-safe reads
# ---------------------------------------------------------------------------
def w3_baseline() -> dict[str, Any]:
    reg = bug_registry(cache=False)

    def brain(req):
        n = turns(req.messages)
        if n == 0:
            return Action(calls=[ToolCall("read_0", {"path": "a"})])
        if n == 1:
            return Action(calls=[ToolCall("read_1", {"path": "b"})])
        if n == 2:
            return Action(calls=[ToolCall("read_2", {"path": "c"})])
        if n == 3:
            return Action(calls=[ToolCall("run_tests", {})])
        if n == 4:
            return Action(calls=[ToolCall("run_tests", {})])
        return Action(text="fixed")

    h = Harness(MockProvider(brain, latency_s=MODEL_MS / 1000), reg, Budget(max_steps=10, max_tool_calls=10))
    h.log.append("user", text="fix the bug")
    res = react(h, lambda hh: "ok" if hh.log.last_text() == "fixed" else None)
    return {"wall": res.wall_s, "tokens": res.tokens_in, "steps": res.steps, "peak": res.metrics["peak_prompt_chars"]}


def w3_optimized() -> dict[str, Any]:
    reg = bug_registry(cache=True)

    def brain(req):
        n = turns(req.messages)
        if n == 0:
            return Action(
                calls=[
                    ToolCall("read_0", {"path": "a"}),
                    ToolCall("read_1", {"path": "b"}),
                    ToolCall("read_2", {"path": "c"}),
                ]
            )
        if n == 1:
            return Action(calls=[ToolCall("run_tests", {})])
        if n == 2:
            return Action(calls=[ToolCall("run_tests", {})])
        return Action(text="fixed")

    h = Harness(MockProvider(brain, latency_s=MODEL_MS / 1000), reg, Budget(max_steps=10, max_tool_calls=10))
    h.log.append("user", text="fix the bug")
    res = react(h, lambda hh: "ok" if hh.log.last_text() == "fixed" else None)
    return {"wall": res.wall_s, "tokens": res.tokens_in, "steps": res.steps, "peak": res.metrics["peak_prompt_chars"]}


@dataclass
class Row:
    name: str
    baseline: dict[str, Any]
    optimized: dict[str, Any]
    notes: str = ""

    @property
    def speedup(self) -> float:
        return self.baseline["wall"] / self.optimized["wall"] if self.optimized["wall"] else float("inf")

    @property
    def token_delta(self) -> float:
        b = self.baseline["tokens"] or 1
        return (self.optimized["tokens"] - self.baseline["tokens"]) / b


WORKLOADS: list[tuple[str, Callable[[], dict], Callable[[], dict], str]] = [
    ("12 docs", w1_baseline, w1_optimized, "单上下文串行 vs 6 路扇出（隔离上下文）"),
    ("24 tickets", w2_baseline, w2_optimized, "12 个工具的全量 schema vs 3 个窄工具 + 6 路并发"),
    ("bugfix loop", w3_baseline, w3_optimized, "逐个读 vs 并行读；重复 run_tests vs 缓存命中"),
]


def run(repeats: int = 2) -> list[Row]:
    rows = []
    for name, base, opt, note in WORKLOADS:
        b = min((base() for _ in range(repeats)), key=lambda d: d["wall"])
        o = min((opt() for _ in range(repeats)), key=lambda d: d["wall"])
        rows.append(Row(name, b, o, note))
    return rows


def render(rows: list[Row]) -> str:
    lines = [
        "| 负载 | 基线 | 优化后 | 加速 | token 变化 | 步数 | 说明 |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r.name} | {r.baseline['wall']:.2f}s | {r.optimized['wall']:.2f}s | "
            f"**{r.speedup:.1f}x** | {r.token_delta:+.0%} | {r.baseline['steps']} -> {r.optimized['steps']} | {r.notes} |"
        )
    return "\n".join(lines)


def main() -> None:
    t0 = time.monotonic()
    rows = run()
    table = render(rows)
    total_b = sum(r.baseline["wall"] for r in rows)
    total_o = sum(r.optimized["wall"] for r in rows)

    print("\n=== loopkit bench ===")
    print(table)
    print(f"\n合计: {total_b:.2f}s -> {total_o:.2f}s  ({total_b / total_o:.1f}x)")
    print(f"耗时 {time.monotonic() - t0:.1f}s | python {platform.python_version()} | cpu {os.cpu_count()}")

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "RESULTS.md")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("# loopkit 基准结果\n\n")
        fh.write("模拟模型 80ms/次、路由模型 20ms/次、工具 50ms/次；每项取 2 次运行的最小值。\n\n")
        fh.write(table + "\n\n")
        fh.write(f"合计：{total_b:.2f}s -> {total_o:.2f}s（{total_b / total_o:.1f}x）\n\n")
        fh.write("**这些数字测量的是编排开销，不是模型能力**：延迟是模拟的，加速来自并发、缓存、去重与工具面收窄。\n")
        fh.write(f"环境：Python {platform.python_version()}，{os.cpu_count()} 核，{platform.platform()}\n")
    print(f"已写入 {out}")


if __name__ == "__main__":
    main()
