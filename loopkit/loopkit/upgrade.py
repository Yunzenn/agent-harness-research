"""Upgrade layer for loops you already have.

This module deliberately does NOT own your control flow. You keep your `while`
loop, your graph, your SDK; you call three things:

    guard = LoopGuard(GuardConfig(max_steps=12))
    box = ToolBox({"read": read_file, "run_tests": run_tests}, guard=guard, cache_ttl=60)

    while True:
        if guard.begin_step(tokens_in=usage):      # budget / wall clock
            break
        try:
            out = box.call("read", {"path": "a.py"})   # cache + repeat guard
        except ToolDenied as exc:                      # denial is data
            ...

That is the whole contract. Everything else is measurement.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional


class ToolDenied(Exception):
    """Raised instead of running a call the guard refused."""

    def __init__(self, reason: str, tool: str = "", args: Optional[dict] = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.tool = tool
        self.args = args or {}


@dataclass
class GuardConfig:
    max_steps: int = 12
    max_tool_calls: int = 40
    max_tokens_in: int = 300_000
    max_wall_s: float = 60.0
    max_repeats: int = 3  # identical (tool, args) calls; 0 disables the check
    cache_ttl_s: float = 0.0  # >0 enables memoization in ToolBox
    require_approval: tuple[str, ...] = ()  # irreversible tools


@dataclass
class LoopStats:
    steps: int = 0
    tool_calls: int = 0
    denied: int = 0
    repeats: int = 0
    cache_hits: int = 0
    tokens_in: int = 0
    wall_s: float = 0.0
    stop_reason: str = ""
    tool_seconds: float = 0.0

    def as_row(self) -> str:
        return (
            f"steps={self.steps:<3} tools={self.tool_calls:<3} cache={self.cache_hits:<3} "
            f"denied={self.denied:<3} tokens={self.tokens_in:<7} wall={self.wall_s:6.2f}s stop={self.stop_reason or '-'}"
        )

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


class LoopGuard:
    """Budgets, repeat detection and approval, for any loop."""

    def __init__(self, config: Optional[GuardConfig] = None, clock: Callable[[], float] = time.monotonic) -> None:
        self.cfg = config or GuardConfig()
        self.clock = clock
        self._started = clock()
        self.stats = LoopStats()
        self._counts: dict[str, int] = {}
        self.events: list[dict[str, Any]] = []
        self._stopped = False

    # -- step boundary ----------------------------------------------------
    def begin_step(self, tokens_in: int = 0) -> Optional[str]:
        """Call at the top of each iteration. Returns a stop reason, or None."""
        self.stats.tokens_in += max(0, int(tokens_in))
        reason = self._budget_reason()
        if reason:
            self._stop(reason)
            return reason
        self.stats.steps += 1
        return None

    def _budget_reason(self) -> Optional[str]:
        if self.stats.steps >= self.cfg.max_steps:
            return "max_steps"
        if self.stats.tool_calls >= self.cfg.max_tool_calls:
            return "max_tool_calls"
        if self.stats.tokens_in >= self.cfg.max_tokens_in:
            return "max_tokens_in"
        if self.clock() - self._started >= self.cfg.max_wall_s:
            return "max_wall_s"
        return None

    def _stop(self, reason: str) -> None:
        if not self._stopped:
            self._stopped = True
            self.stats.stop_reason = reason
            self.events.append({"event": "stop", "reason": reason, "at_s": round(self.clock() - self._started, 3)})

    # -- tool gate --------------------------------------------------------
    def allow(self, tool: str, args: dict[str, Any]) -> Optional[str]:
        """Returns a denial reason, or None to allow."""
        budget = self._budget_reason()
        if budget:
            self._stop(budget)
            self.stats.denied += 1
            self._event("deny", tool=tool, reason=budget)
            return f"budget exhausted: {budget}"

        if tool in self.cfg.require_approval:
            self.stats.denied += 1
            self._event("deny", tool=tool, reason="approval_required")
            return f"{tool} requires human approval"

        if self.cfg.max_repeats > 0:
            key = tool + "\x00" + json.dumps(args, sort_keys=True, default=str)
            n = self._counts.get(key, 0) + 1
            self._counts[key] = n
            if n > self.cfg.max_repeats:
                self.stats.denied += 1
                self.stats.repeats += 1
                self._event("deny", tool=tool, reason="repeat", count=n)
                return (
                    f"repeated call to {tool} ({n}x with identical arguments); "
                    "read the previous result or change the arguments instead"
                )
        return None

    def record(self, tool: str, ok: bool, elapsed_s: float) -> None:
        self.stats.tool_calls += 1
        self.stats.tool_seconds += elapsed_s
        self._event("tool", tool=tool, ok=ok, elapsed_s=round(elapsed_s, 4))

    # -- finish -----------------------------------------------------------
    def finish(self, reason: str = "ok") -> LoopStats:
        self.stats.wall_s = round(self.clock() - self._started, 3)
        if not self._stopped:
            self.stats.stop_reason = reason
        return self.stats

    def _event(self, kind: str, **data: Any) -> None:
        if len(self.events) < 10_000:
            self.events.append({"event": kind, "at_s": round(self.clock() - self._started, 3), **data})

    def trace(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            for ev in self.events:
                fh.write(json.dumps(ev, ensure_ascii=False) + "\n")


class ToolBox:
    """Wrap plain callables: memoization, batch de-duplication, bounded parallelism.

    The wrapped functions keep their original signature: `box.call(name, args)`
    or `box.batch([(name, args), ...])`. Nothing about your loop changes.
    """

    def __init__(
        self,
        tools: dict[str, Callable[..., Any]],
        guard: Optional[LoopGuard] = None,
        cache_ttl_s: Optional[float] = None,
        parallel: bool = True,
        max_workers: int = 8,
        idempotent: Optional[Iterable[str]] = None,
    ) -> None:
        self.tools = dict(tools)
        self.guard = guard or LoopGuard()
        self.cache_ttl_s = self.guard.cfg.cache_ttl_s if cache_ttl_s is None else cache_ttl_s
        self.parallel = parallel
        self.max_workers = max_workers
        self.idempotent = set(self.tools) if idempotent is None else set(idempotent)
        self._cache: dict[str, tuple[float, Any]] = {}

    # -- single call -------------------------------------------------------
    @staticmethod
    def _key(name: str, args: dict[str, Any]) -> str:
        return name + "\x00" + json.dumps(args, sort_keys=True, default=str)

    def call(self, name: str, args: Optional[dict[str, Any]] = None) -> Any:
        args = args or {}
        fn = self.tools.get(name)
        if fn is None:
            raise KeyError(f"unknown tool: {name}")
        reason = self.guard.allow(name, args)
        if reason:
            raise ToolDenied(reason, name, args)

        key = self._key(name, args)
        if self.cache_ttl_s > 0 and name in self.idempotent:
            hit = self._cache.get(key)
            if hit and hit[0] > time.monotonic():
                self.guard.stats.cache_hits += 1
                self.guard._event("cache_hit", tool=name)
                return hit[1]

        started = time.monotonic()
        try:
            value = fn(**args)
        except Exception:
            self.guard.record(name, False, time.monotonic() - started)
            raise
        elapsed = time.monotonic() - started
        self.guard.record(name, True, elapsed)
        if self.cache_ttl_s > 0 and name in self.idempotent:
            self._cache[key] = (time.monotonic() + self.cache_ttl_s, value)
        return value

    # -- batch -------------------------------------------------------------
    def batch(self, calls: list[tuple[str, dict[str, Any]]]) -> list[Any]:
        """De-duplicate identical calls, run the rest concurrently, keep order."""
        results: list[Any] = [None] * len(calls)
        first_index: dict[str, int] = {}
        pending: list[int] = []

        for i, (name, args) in enumerate(calls):
            if name in self.idempotent and self.cache_ttl_s > 0:
                key = self._key(name, args)
                if key in first_index:
                    continue  # filled after its source completes
                first_index[key] = i
            pending.append(i)

        def one(i: int) -> None:
            name, args = calls[i]
            try:
                results[i] = self.call(name, args)
            except ToolDenied as exc:
                results[i] = exc

        if self.parallel and len(pending) > 1:
            with ThreadPoolExecutor(max_workers=max(1, min(self.max_workers, len(pending)))) as pool:
                list(pool.map(one, pending))
        else:
            for i in pending:
                one(i)

        for i, (name, args) in enumerate(calls):
            if results[i] is None:
                key = self._key(name, args)
                src = first_index.get(key)
                if src is not None:
                    results[i] = results[src]
        return results

    def stats(self) -> LoopStats:
        return self.guard.stats
