"""Uplift benchmark: the same loop, with and without the upgrade layer.

Nothing in the loop changes between the two columns. Only the call layer is
swapped: a plain dict of functions vs `ToolBox` + `LoopGuard`. That is the whole
claim of this package — you keep your loop, you add a layer, you get numbers.

    python bench/uplift_bench.py
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loopkit.upgrade import GuardConfig, LoopGuard, ToolBox, ToolDenied  # noqa: E402

TOOL_MS = 40


# ---------------------------------------------------------------------------
# a world with slow tools and per-tool execution counters
# ---------------------------------------------------------------------------
class World:
    def __init__(self) -> None:
        self.executions: dict[str, int] = {}
        self.ticket_reads = 0

    def _count(self, name: str) -> None:
        self.executions[name] = self.executions.get(name, 0) + 1

    def get_ticket(self, tid: str) -> dict:
        self._count("get_ticket")
        time.sleep(TOOL_MS / 1000)
        self.ticket_reads += 1
        return {"tid": tid, "status": "open"}

    def run_tests(self, attempt: int = 0) -> str:
        self._count("run_tests")
        time.sleep(TOOL_MS / 500)
        return "fail"  # never converges: the loop must be stopped from outside

    def read(self, path: str) -> str:
        self._count("read")
        time.sleep(TOOL_MS / 1000)
        return "content of " + path


def plain_tools(w: World) -> dict:
    return {"get_ticket": w.get_ticket, "run_tests": w.run_tests, "read": w.read}


# ---------------------------------------------------------------------------
# the loops (identical in both columns)
# ---------------------------------------------------------------------------
def loop_tickets(call, n=12) -> dict:
    """Reads each ticket twice — a very common accidental duplicate."""
    for i in range(n):
        call("get_ticket", {"tid": f"T{i}"})
        call("get_ticket", {"tid": f"T{i}"})
    return {"iterations": n}


def loop_runaway(call, limit=50) -> dict:
    """Never converges; arguments change every time so repeat detection is moot."""
    i = 0
    while i < limit:
        try:
            call("run_tests", {"attempt": i})
        except ToolDenied:
            pass
        i += 1
    return {"iterations": i}


def loop_repeat(call, n=10) -> dict:
    """Hammers one call with identical arguments."""
    for _ in range(n):
        try:
            call("read", {"path": "same.py"})
        except ToolDenied:
            pass
    return {"iterations": n}


def run_bare(world: World, loop, **kw):
    tools = plain_tools(world)
    calls = {"n": 0}

    def call(name, args):
        calls["n"] += 1
        return tools[name](**args)

    t0 = time.monotonic()
    loop(call, **kw)
    return {"calls": calls["n"], "exec": sum(world.executions.values()), "wall": time.monotonic() - t0, "stop": "-"}


def run_upgraded(world: World, loop, guard_cfg: GuardConfig, cache_ttl: float, **kw):
    guard = LoopGuard(guard_cfg)
    box = ToolBox(plain_tools(world), guard=guard, cache_ttl_s=cache_ttl)
    calls = {"n": 0}

    def call(name, args):
        calls["n"] += 1
        return box.call(name, args)

    t0 = time.monotonic()
    loop(call, **kw)
    stats = guard.finish("loop_exit")
    return {
        "calls": calls["n"],
        "exec": sum(world.executions.values()),
        "wall": time.monotonic() - t0,
        "stop": stats.stop_reason,
        "cache": stats.cache_hits,
        "denied": stats.denied,
    }


@dataclass
class Row:
    name: str
    bare: dict
    up: dict

    @property
    def call_delta(self) -> float:
        return 1 - self.up["exec"] / self.bare["exec"] if self.bare["exec"] else 0.0

    @property
    def wall_delta(self) -> float:
        return 1 - self.up["wall"] / self.bare["wall"] if self.bare["wall"] else 0.0


def main() -> None:
    rows: list[Row] = []

    w = World()
    bare = run_bare(w, loop_tickets)
    w = World()
    up = run_upgraded(w, loop_tickets, GuardConfig(max_steps=100, max_repeats=0, cache_ttl_s=60), cache_ttl=60)
    rows.append(Row("12 tickets (duplicate reads)", bare, up))

    w = World()
    bare = run_bare(w, loop_runaway)
    w = World()
    up = run_upgraded(w, loop_runaway, GuardConfig(max_steps=12, max_tool_calls=12), cache_ttl=0)
    rows.append(Row("runaway retry loop", bare, up))

    w = World()
    bare = run_bare(w, loop_repeat)
    w = World()
    up = run_upgraded(w, loop_repeat, GuardConfig(max_steps=100, max_repeats=3), cache_ttl=0)
    rows.append(Row("identical call x10", bare, up))

    print("\n=== loopkit upgrade bench (same loop, only the call layer differs) ===\n")
    print(f"{'scenario':<34}{'bare exec':>10}{'upg exec':>9}{'Δexec':>8}{'bare s':>8}{'upg s':>8}{'Δwall':>8}  stop")
    for r in rows:
        print(
            f"{r.name:<34}{r.bare['exec']:>10}{r.up['exec']:>9}{r.call_delta:>7.0%}"
            f"{r.bare['wall']:>8.2f}{r.up['wall']:>8.2f}{r.wall_delta:>7.0%}  {r.up['stop']}"
        )
    print("\n说明：Δexec 是真实执行次数的下降（缓存/去重/拒绝），Δwall 是墙钟下降。")
    print("runaway 场景里裸循环跑到 50 次人为上限；加装后第 12 次调用即被预算拒绝并停止。")


if __name__ == "__main__":
    main()
