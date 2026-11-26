"""Core value types shared by every module. No behaviour, no imports from loopkit."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class Event:
    seq: int
    kind: str
    data: dict[str, Any]


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any]
    id: str = field(default_factory=lambda: "c" + uuid.uuid4().hex[:8])


@dataclass
class ToolResult:
    call_id: str
    name: str
    ok: bool
    content: str
    code: str = "OK"
    elapsed_s: float = 0.0
    cached: bool = False

    def as_message(self) -> dict[str, Any]:
        return {"role": "tool", "tool_call_id": self.call_id, "name": self.name, "content": self.content}


@dataclass
class Action:
    """What a model turn produced: some text, and/or some tool calls."""

    text: Optional[str] = None
    calls: list[ToolCall] = field(default_factory=list)


@dataclass
class Budget:
    """Four independent ceilings. A run stops at whichever is hit first."""

    max_steps: int = 12
    max_tool_calls: int = 40
    max_tokens_in: int = 300_000
    max_seconds: float = 120.0
    started_at: float = field(default_factory=time.monotonic)

    def hit(self, steps: int, tool_calls: int, tokens_in: int) -> Optional[str]:
        if steps >= self.max_steps:
            return "max_steps"
        if tool_calls >= self.max_tool_calls:
            return "max_tool_calls"
        if tokens_in >= self.max_tokens_in:
            return "max_tokens_in"
        if time.monotonic() - self.started_at >= self.max_seconds:
            return "max_seconds"
        return None


@dataclass
class Stop:
    stop: bool
    reason: str = ""
    status: str = "ok"  # ok | budget | failed | awaiting_human


@dataclass
class RunResult:
    status: str
    reason: str
    text: str
    steps: int
    tool_calls: int
    tokens_in: int
    tokens_out: int
    wall_s: float
    log: Any = None
    metrics: dict[str, Any] = field(default_factory=dict)

    def line(self) -> str:
        return (
            f"status={self.status:<15} reason={self.reason:<18} steps={self.steps:<3} "
            f"tools={self.tool_calls:<3} tok_in={self.tokens_in:<7} wall={self.wall_s:6.2f}s"
        )


@dataclass
class LLMRequest:
    system: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    model: str
    temperature: float = 0.0
    max_tokens: Optional[int] = None

    def approx_chars(self) -> int:
        import json

        return len(json.dumps({"system": self.system, "messages": self.messages, "tools": self.tools}, ensure_ascii=False))


@dataclass
class LLMResponse:
    text: Optional[str]
    tool_calls: list[ToolCall]
    tokens_in: int
    tokens_out: int
    latency_s: float = 0.0
    model: str = ""


Predicate = Callable[[Any], Optional[str]]
