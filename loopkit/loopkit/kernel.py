"""The kernel: one log, one budget, one registry view, one loop.

    assemble -> model -> append -> dispatch -> stop?

Everything above this line (routing, fan-out, planning) is a controller in
`patterns.py`. If a workflow needs to change this file, the kernel is wrong.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Optional

from .llm import Provider
from .session import SessionLog
from .tools import Registry
from .types import Action, Budget, RunResult, Stop


class Harness:
    def __init__(
        self,
        provider: Provider,
        tools: Registry,
        budget: Optional[Budget] = None,
        system: str = "",
        model: str = "mock",
        log: Optional[SessionLog] = None,
        max_parallel_tools: int = 8,
        trace: Optional[Callable[[str, dict[str, Any]], None]] = None,
    ) -> None:
        self.provider = provider
        self.tools = tools
        self.budget = budget or Budget()
        self.system = system
        self.model = model
        self.log = log or SessionLog()
        self.max_parallel_tools = max_parallel_tools
        self.trace = trace

        self.steps = 0
        self.tool_call_count = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self.prompt_chars = 0
        self.prefix_chars = 0
        self.peak_prompt_chars = 0
        self._started = time.monotonic()
        self._awaiting_human: Optional[dict[str, Any]] = None

    # -- prompt assembly ---------------------------------------------------
    def assemble(self):
        from .types import LLMRequest

        tools = self.tools.schemas()
        body = self.log.messages()
        prefix = len(json.dumps({"system": self.system, "tools": tools}, ensure_ascii=False))
        req = LLMRequest(system=self.system, messages=body, tools=tools, model=self.model)
        size = req.approx_chars()
        self.prompt_chars += size
        self.prefix_chars += prefix
        self.peak_prompt_chars = max(self.peak_prompt_chars, size)
        if self.trace:
            self.trace("assemble", {"step": self.steps, "chars": size, "tools": len(tools)})
        return req

    # -- one step ----------------------------------------------------------
    def step(self) -> Action:
        req = self.assemble()
        resp = self.provider.complete(req)
        self.tokens_in += resp.tokens_in
        self.tokens_out += resp.tokens_out
        self.steps += 1

        action = Action(text=resp.text, calls=list(resp.tool_calls))
        if action.calls:
            self.log.append(
                "assistant_calls",
                text=action.text or "",
                calls=[
                    {"id": c.id, "name": c.name, "args": c.args, "args_json": json.dumps(c.args, ensure_ascii=False)}
                    for c in action.calls
                ],
            )
            self.tool_call_count += len(action.calls)
            results = self.tools.execute_batch(action.calls, max_parallel=self.max_parallel_tools)
            for res in results:
                self.log.append(
                    "tool_result",
                    call_id=res.call_id,
                    name=res.name,
                    content=res.content,
                    ok=res.ok,
                    code=res.code,
                    cached=res.cached,
                    elapsed_s=round(res.elapsed_s, 4),
                )
            if self.trace:
                self.trace("tools", {"step": self.steps, "n": len(results), "ok": sum(1 for r in results if r.ok)})
        elif action.text is not None:
            self.log.append("assistant_text", text=action.text)
            if self.trace:
                self.trace("text", {"step": self.steps, "chars": len(action.text)})
        else:
            self.log.append("note", text="empty turn")
        return action

    # -- the loop ----------------------------------------------------------
    def run(self, stop: Callable[["Harness"], Stop]) -> RunResult:
        t0 = time.monotonic()
        while True:
            hit = self.budget.hit(self.steps, self.tool_call_count, self.tokens_in)
            if hit:
                return self._result("budget", hit, self.log.last_text(), t0)
            if self._awaiting_human:
                return self._result("awaiting_human", "approval_required", self.log.last_text(), t0)

            action = self.step()
            decision = stop(self)
            if decision.stop:
                return self._result(decision.status, decision.reason, action.text or self.log.last_text(), t0)
            if action.text is not None and not action.calls:
                # Text without calls and the judge is not satisfied: nudge, do not spin.
                self.log.append("note", text="text-only turn without passing the stop predicate")
            if self.trace:
                self.trace("stop?", {"step": self.steps, "reason": decision.reason})

    # -- helpers -----------------------------------------------------------
    def _result(self, status: str, reason: str, text: str, t0: float) -> RunResult:
        wall = time.monotonic() - t0
        stats = self.tools.stats
        return RunResult(
            status=status,
            reason=reason,
            text=text,
            steps=self.steps,
            tool_calls=self.tool_call_count,
            tokens_in=self.tokens_in,
            tokens_out=self.tokens_out,
            wall_s=wall,
            log=self.log,
            metrics={
                "prompt_chars": self.prompt_chars,
                "peak_prompt_chars": self.peak_prompt_chars,
                "prefix_ratio": round(self.prefix_chars / self.prompt_chars, 3) if self.prompt_chars else 0.0,
                "cache_hits": stats.cache_hits,
                "deduped": stats.deduped,
                "parallel_batches": stats.parallel_batches,
                "tool_seconds": round(stats.tool_seconds, 3),
                "provider": getattr(self.provider, "name", "?"),
                "model": self.model,
            },
        )

    def request_human(self, action: str, reason: str, payload: Optional[dict[str, Any]] = None) -> None:
        self._awaiting_human = {"action": action, "reason": reason, **(payload or {})}
        self.log.append("gate", action=action, decision="pending", reason=reason, payload=payload or {})

    def resume_from(self, checkpoint: dict[str, Any], approval: Optional[dict[str, Any]] = None) -> None:
        self.log = SessionLog.restore(checkpoint)
        self._awaiting_human = None
        if approval is not None:
            self.log.append("gate", decision="approved", **approval)
