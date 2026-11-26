"""Tool registry: validation, guards, caching, de-duplication, bounded parallelism.

The execution pipeline is fixed and small on purpose:

    lookup -> validate -> guard -> cache? -> run (with timeout) -> shape

Every failure becomes a ToolResult with ok=False, never an exception. A loop can
only stay coherent if every call gets exactly one result.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

from .types import ToolCall, ToolResult


class ToolError(Exception):
    """A failure the model is allowed to see and recover from."""


@dataclass
class Param:
    type: str
    description: str = ""
    required: bool = True


@dataclass
class Tool:
    name: str
    description: str
    params: dict[str, Param]
    fn: Callable[..., Any]
    read_only: bool = True
    idempotent: bool = True
    parallel_safe: bool = True
    destructive: bool = False
    cache_ttl_s: float = 0.0
    timeout_s: float = 0.0

    def schema(self) -> dict[str, Any]:
        props = {n: {"type": p.type, "description": p.description} for n, p in self.params.items()}
        required = [n for n, p in self.params.items() if p.required]
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {"type": "object", "properties": props, "required": required},
            },
        }

    def cacheable(self) -> bool:
        return self.idempotent and self.cache_ttl_s > 0 and self.read_only


@dataclass
class Stats:
    calls: int = 0
    executed: int = 0
    cache_hits: int = 0
    deduped: int = 0
    denied: int = 0
    errors: int = 0
    timeouts: int = 0
    tool_seconds: float = 0.0
    wall_seconds: float = 0.0
    batches: int = 0
    parallel_batches: int = 0

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


class Registry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self.allow: Optional[set[str]] = None
        self.guards: list[Callable[[Tool, dict[str, Any]], Optional[str]]] = []
        self.stats = Stats()
        self._cache: dict[str, tuple[float, str]] = {}

    # -- registration ------------------------------------------------------
    def register(self, tool: Tool) -> "Registry":
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool name: {tool.name}")
        self._tools[tool.name] = tool
        return self

    def tool(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def restrict(self, names: Optional[Iterable[str]]) -> "Registry":
        """Narrow the visible surface. Schemas AND execution both honour it."""
        self.allow = None if names is None else set(names)
        return self

    def visible(self) -> list[Tool]:
        return [t for n, t in self._tools.items() if self.allow is None or n in self.allow]

    def schemas(self) -> list[dict[str, Any]]:
        return [t.schema() for t in self.visible()]

    def clear_cache(self) -> None:
        self._cache.clear()

    # -- execution ---------------------------------------------------------
    def _validate(self, tool: Tool, args: dict[str, Any]) -> Optional[str]:
        for name, p in tool.params.items():
            if p.required and name not in args:
                return f"missing required parameter '{name}'"
        for name in args:
            if name not in tool.params:
                return f"unknown parameter '{name}'"
        return None

    @staticmethod
    def _key(name: str, args: dict[str, Any]) -> str:
        return name + "\x00" + json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)

    def _render(self, value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, (dict, list, int, float, bool)) or value is None:
            return json.dumps(value, ensure_ascii=False, default=str)
        return str(value)

    def _run_once(self, tool: Tool, args: dict[str, Any]) -> tuple[bool, str, str]:
        started = time.monotonic()
        try:
            if tool.timeout_s > 0:
                with ThreadPoolExecutor(max_workers=1) as pool:
                    value = pool.submit(lambda: tool.fn(**args)).result(timeout=tool.timeout_s)
            else:
                value = tool.fn(**args)
        except ToolError as exc:
            self.stats.errors += 1
            return False, f"Error: {exc}", "TOOL_FAILED"
        except TimeoutError:
            self.stats.timeouts += 1
            return False, f"Error: tool timed out after {tool.timeout_s:.0f}s", "TOOL_TIMEOUT"
        except Exception as exc:  # noqa: BLE001 - a tool bug must not kill the run
            self.stats.errors += 1
            return False, f"Error: {type(exc).__name__}: {exc}", "TOOL_CRASHED"
        finally:
            self.stats.tool_seconds += time.monotonic() - started
            self.stats.executed += 1
        return True, self._render(value), "OK"

    def execute(self, call: ToolCall) -> ToolResult:
        self.stats.calls += 1
        tool = self._tools.get(call.name)
        if tool is None or (self.allow is not None and call.name not in self.allow):
            self.stats.denied += 1
            return ToolResult(call.id, call.name, False, f"Error: unknown or unavailable tool '{call.name}'", "UNKNOWN_TOOL")

        bad = self._validate(tool, call.args)
        if bad:
            self.stats.denied += 1
            return ToolResult(call.id, call.name, False, f"Error: {bad}", "BAD_ARGS")

        for guard in self.guards:
            reason = guard(tool, call.args)
            if reason:
                self.stats.denied += 1
                return ToolResult(call.id, call.name, False, f"Error: denied by policy: {reason}", "DENIED")

        key = self._key(call.name, call.args)
        if tool.cacheable():
            hit = self._cache.get(key)
            if hit and hit[0] > time.monotonic():
                self.stats.cache_hits += 1
                return ToolResult(call.id, call.name, True, hit[1], "OK", 0.0, cached=True)

        ok, content, code = self._run_once(tool, call.args)
        if ok and tool.cacheable():
            self._cache[key] = (time.monotonic() + tool.cache_ttl_s, content)
        return ToolResult(call.id, call.name, ok, content, code)

    def execute_batch(self, calls: list[ToolCall], max_parallel: int = 8) -> list[ToolResult]:
        """Run a batch with de-duplication and bounded concurrency.

        - identical (name, canonical args) within one batch run once, if idempotent;
        - parallel-safe calls are grouped and run concurrently;
        - non-parallel-safe calls act as barriers and keep model order.
        Results always come back in the model's order.
        """
        self.stats.batches += 1
        wall_start = time.monotonic()
        n = len(calls)
        results: list[Optional[ToolResult]] = [None] * n

        # 1. map duplicates onto their source call
        key_to_src: dict[str, int] = {}
        src_for = list(range(n))
        dups: dict[int, list[int]] = {}
        for i, call in enumerate(calls):
            tool = self._tools.get(call.name)
            if tool is None or not tool.idempotent:
                continue
            key = self._key(call.name, call.args)
            if key in key_to_src:
                src = key_to_src[key]
                src_for[i] = src
                dups.setdefault(src, []).append(i)
                self.stats.deduped += 1
            else:
                key_to_src[key] = i

        # 2. execute unique calls: parallel where safe, barriers where not
        group: list[int] = []

        def flush() -> None:
            if not group:
                return
            if len(group) == 1:
                i = group[0]
                results[i] = self.execute(calls[i])
            else:
                self.stats.parallel_batches += 1
                with ThreadPoolExecutor(max_workers=max(1, min(max_parallel, len(group)))) as pool:
                    futures = [(i, pool.submit(self.execute, calls[i])) for i in group]
                    for i, fut in futures:
                        results[i] = fut.result()
            group.clear()

        for i in range(n):
            if src_for[i] != i:
                continue
            tool = self._tools.get(calls[i].name)
            safe = tool is not None and tool.parallel_safe and not tool.destructive
            if safe:
                group.append(i)
            else:
                flush()
                results[i] = self.execute(calls[i])
        flush()

        # 3. fan results out to duplicates, preserving each call id
        for src, idxs in dups.items():
            base = results[src]
            for j in idxs:
                if base is None:
                    results[j] = self.execute(calls[j])
                else:
                    results[j] = ToolResult(calls[j].id, calls[j].name, base.ok, base.content, base.code, 0.0, cached=True)

        self.stats.wall_seconds += time.monotonic() - wall_start
        return [r for r in results if r is not None]
