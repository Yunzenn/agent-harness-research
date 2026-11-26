"""A minimal agent-harness kernel, for experiments (not production).

The point of this file is to make the *shape* of an agent loop explicit and
small enough to argue about:

    assemble(context) -> model -> parse -> guard -> dispatch tools
        -> append results (always paired) -> stop?

Everything else -- planning, fan-out, routing, human gates -- is a *controller*
built on top of that kernel. That separation is the thesis of the accompanying
notes: harnesses differ in where policy lives, not in the loop itself.

No third-party dependencies. Deterministic, offline: the "model" is a scripted
policy so runs are reproducible and free.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

# ---------------------------------------------------------------------------
# 1. Session log: append-only, the single source of truth
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Event:
    seq: int
    kind: str  # user | assistant | tool_call | tool_result | system | note | gate
    payload: dict

    def to_json(self) -> dict:
        return {"seq": self.seq, "kind": self.kind, **self.payload}


class SessionLog:
    """Append-only event log plus a projection into model messages.

    Why a log and not a message array: the log can hold things the model must
    never see (notes, gate decisions, cost), can be replayed, and can be
    checkpointed. The model only ever sees `messages()`.
    """

    def __init__(self) -> None:
        self._events: list[Event] = []

    def append(self, kind: str, **payload: Any) -> Event:
        ev = Event(seq=len(self._events), kind=kind, payload=payload)
        self._events.append(ev)
        return ev

    @property
    def events(self) -> list[Event]:
        return list(self._events)

    def messages(self) -> list[dict]:
        out: list[dict] = []
        for ev in self._events:
            if ev.kind == "user":
                out.append({"role": "user", "content": ev.payload["text"]})
            elif ev.kind == "assistant":
                out.append({"role": "assistant", "content": ev.payload.get("text", "")})
            elif ev.kind == "tool_call":
                out.append(
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {"id": ev.payload["call_id"], "name": ev.payload["name"], "args": ev.payload["args"]}
                        ],
                    }
                )
            elif ev.kind == "tool_result":
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": ev.payload["call_id"],
                        "name": ev.payload["name"],
                        "content": ev.payload["content"],
                    }
                )
        return out

    def transcript(self) -> str:
        """Cheap rendering used by scripted policies and by humans reading traces."""
        lines = []
        for ev in self._events:
            if ev.kind in ("user", "assistant"):
                lines.append(f"[{ev.kind}] {ev.payload.get('text','')}")
            elif ev.kind == "tool_call":
                lines.append(f"[call] {ev.payload['name']}({json.dumps(ev.payload['args'], ensure_ascii=False)})")
            elif ev.kind == "tool_result":
                lines.append(f"[result:{ev.payload['name']}] {ev.payload['content']}")
            elif ev.kind == "note":
                lines.append(f"[note] {ev.payload.get('text','')}")
            elif ev.kind == "gate":
                lines.append(f"[gate] {ev.payload}")
        return "\n".join(lines)

    def snapshot(self) -> dict:
        return {"events": [e.to_json() for e in self._events]}

    @classmethod
    def restore(cls, snap: dict) -> "SessionLog":
        log = cls()
        for raw in snap["events"]:
            payload = {k: v for k, v in raw.items() if k not in ("seq", "kind")}
            log._events.append(Event(seq=raw["seq"], kind=raw["kind"], payload=payload))
        return log


# ---------------------------------------------------------------------------
# 2. Tools: typed contract + a fixed execution pipeline
# ---------------------------------------------------------------------------


class ToolError(Exception):
    """A tool failure the model is allowed to see and recover from."""


@dataclass
class Tool:
    name: str
    description: str
    params: dict[str, str]
    fn: Callable[..., Any]
    read_only: bool = True
    idempotent: bool = True
    parallel_safe: bool = True
    destructive: bool = False
    optional: tuple[str, ...] = ()

    def schema(self) -> dict:
        # This object IS prompt engineering: it is what the model reads.
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.params,
            "read_only": self.read_only,
            "idempotent": self.idempotent,
            "parallel_safe": self.parallel_safe,
        }


@dataclass
class ToolCall:
    call_id: str
    name: str
    args: dict


@dataclass
class ToolResult:
    call_id: str
    name: str
    ok: bool
    content: str
    code: str = "OK"


class Registry:
    """Registry + guard + dispatch.

    Pipeline (mirrors what production harnesses do):
      validate args -> guards (monotonic deny) -> execute -> shape result
    A failing tool never raises out of the loop: it becomes an `Error: ...`
    result so the transcript stays well-formed and the model can recover.
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self.guards: list[Callable[[Tool, dict], Optional[str]]] = []
        self.audit: list[dict] = []

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def schemas(self, allow: Optional[Iterable[str]] = None) -> list[dict]:
        names = set(allow) if allow is not None else set(self._tools)
        return [t.schema() for n, t in self._tools.items() if n in names]

    def validate(self, tool: Tool, args: dict) -> Optional[str]:
        for pname in tool.params:
            if pname not in args and pname not in tool.optional:
                return f"missing required parameter '{pname}'"
        for k in args:
            if k not in tool.params:
                return f"unknown parameter '{k}'"
        return None

    def execute(self, call: ToolCall) -> ToolResult:
        tool = self._tools.get(call.name)
        if tool is None:
            return ToolResult(call.call_id, call.name, False, f"Error: unknown tool '{call.name}'", "UNKNOWN_TOOL")
        bad = self.validate(tool, call.args)
        if bad:
            return ToolResult(call.call_id, call.name, False, f"Error: {bad}", "BAD_ARGS")
        for guard in self.guards:
            reason = guard(tool, call.args)
            if reason:
                self.audit.append({"call": call.name, "args": call.args, "denied": reason})
                return ToolResult(call.call_id, call.name, False, f"Error: denied by policy: {reason}", "DENIED")
        try:
            value = tool.fn(**call.args)
        except ToolError as exc:
            return ToolResult(call.call_id, call.name, False, f"Error: {exc}", "TOOL_FAILED")
        except Exception as exc:  # noqa: BLE001 - tool bugs must not kill the turn
            return ToolResult(call.call_id, call.name, False, f"Error: {type(exc).__name__}: {exc}", "TOOL_CRASHED")
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        return ToolResult(call.call_id, call.name, True, text, "OK")

    def execute_batch(self, calls: list[ToolCall], max_parallel: int = 10) -> list[ToolResult]:
        """Results stay in model order even if execution were concurrent."""
        return [self.execute(c) for c in calls[:max_parallel]]


# ---------------------------------------------------------------------------
# 3. Budget and stop conditions -- first-class, not afterthoughts
# ---------------------------------------------------------------------------


@dataclass
class Budget:
    max_steps: int = 8
    max_tool_calls: int = 24
    max_prompt_chars: int = 400_000  # cumulative re-sent context
    wall_clock_s: float = 60.0
    started_at: float = field(default_factory=time.time)

    def exhausted(self) -> Optional[str]:
        if time.time() - self.started_at > self.wall_clock_s:
            return "wall_clock"
        return None


@dataclass
class StopDecision:
    stop: bool
    reason: str
    status: str = "ok"  # ok | budget | failed | awaiting_human


# ---------------------------------------------------------------------------
# 4. Policies: the only place a "model" appears
# ---------------------------------------------------------------------------


@dataclass
class View:
    """Everything a policy is allowed to see at one step."""

    task: str
    log: SessionLog
    step: int
    tools: list[dict]
    budget: Budget
    scratch: dict


@dataclass
class Action:
    calls: list[dict] = field(default_factory=list)  # [{"name":..., "args":...}]
    final: Optional[str] = None
    note: str = ""


class Policy:
    def decide(self, view: View) -> Action:  # pragma: no cover - interface
        raise NotImplementedError


class ScriptedPolicy(Policy):
    """A deterministic stand-in for an LLM.

    `brain(view, state)` returns an Action. Kept dumb on purpose: the
    interesting behaviour lives in the harness/controller, which is exactly
    what we are studying.
    """

    def __init__(self, brain: Callable[[View, dict], Action]) -> None:
        self.brain = brain
        self.state: dict = {}

    def decide(self, view: View) -> Action:
        view.scratch.update(self.state)
        action = self.brain(view, self.state)
        return action


# ---------------------------------------------------------------------------
# 5. The kernel loop
# ---------------------------------------------------------------------------


def est_chars(obj: Any) -> int:
    return len(json.dumps(obj, ensure_ascii=False))


@dataclass
class RunResult:
    status: str
    reason: str
    final: str
    steps: int
    tool_calls: int
    prompt_chars: int
    log: SessionLog
    peak_prompt_chars: int = 0
    metrics: dict = field(default_factory=dict)

    def summary(self) -> str:
        return (
            f"status={self.status} reason={self.reason} steps={self.steps} "
            f"tool_calls={self.tool_calls} prompt_chars={self.prompt_chars}"
        )


class Harness:
    """One agent: one log, one budget, one registry view."""

    def __init__(
        self,
        policy: Policy,
        registry: Registry,
        budget: Optional[Budget] = None,
        log: Optional[SessionLog] = None,
        allow: Optional[Iterable[str]] = None,
        system: str = "",
    ) -> None:
        self.policy = policy
        self.registry = registry
        self.budget = budget or Budget()
        self.log = log or SessionLog()
        self.allow = list(allow) if allow is not None else None
        self.system = system
        self.steps = 0
        self.tool_calls = 0
        self.prompt_chars = 0
        self.peak_prompt_chars = 0
        self._call_seq = 0

    # -- prompt assembly ---------------------------------------------------
    def assemble(self) -> dict:
        msgs = self.log.messages()
        schemas = self.registry.schemas(self.allow)
        prompt = {"system": self.system, "messages": msgs, "tools": schemas}
        size = est_chars(prompt)
        self.prompt_chars += size
        self.peak_prompt_chars = max(self.peak_prompt_chars, size)
        return prompt

    def view(self) -> View:
        return View(
            task=self.log.events[0].payload.get("text", "") if self.log.events else "",
            log=self.log,
            step=self.steps,
            tools=self.registry.schemas(self.allow),
            budget=self.budget,
            scratch={},
        )

    # -- one step ----------------------------------------------------------
    def step(self) -> Action:
        self.assemble()  # cost is charged whether or not the model uses it
        action = self.policy.decide(self.view())
        self.steps += 1

        if action.final is not None:
            self.log.append("assistant", text=action.final)
            if action.note:
                self.log.append("note", text=action.note)
            return action

        if not action.calls:
            # A model that returns neither text nor a call is a protocol error;
            # silence must be converted into something the loop can act on.
            self.log.append("assistant", text="")
            self.log.append("note", text="empty action; treating as no-op")
            return action

        for raw in action.calls:
            self._call_seq += 1
            self.log.append(
                "tool_call",
                call_id=f"c{self._call_seq}",
                name=raw["name"],
                args=raw.get("args", {}),
            )
        calls = [
            ToolCall(call_id=f"c{self._call_seq - len(action.calls) + i + 1}", name=r["name"], args=r.get("args", {}))
            for i, r in enumerate(action.calls)
        ]
        results = self.registry.execute_batch(calls)
        self.tool_calls += len(results)
        for res in results:
            # Pairing invariant: every call gets exactly one result, even on
            # denial/crash. Unpaired history is what breaks providers.
            self.log.append(
                "tool_result",
                call_id=res.call_id,
                name=res.name,
                content=res.content,
                ok=res.ok,
                code=res.code,
            )
        return action

    def budget_stop(self) -> Optional[str]:
        if self.steps >= self.budget.max_steps:
            return "max_steps"
        if self.tool_calls >= self.budget.max_tool_calls:
            return "max_tool_calls"
        if self.prompt_chars >= self.budget.max_prompt_chars:
            return "max_prompt_chars"
        return self.budget.exhausted()

    # -- the loop ----------------------------------------------------------
    def run(
        self,
        stop: Callable[[View], StopDecision],
        on_event: Optional[Callable[[Event], None]] = None,
    ) -> RunResult:
        while True:
            hard = self.budget_stop()
            if hard:
                return self._result("budget", hard, "")
            before = len(self.log.events)
            action = self.step()
            if on_event:
                for ev in self.log.events[before:]:
                    on_event(ev)
            if action.final is not None:
                return self._result("ok", "final_answer", action.final)
            decision = stop(self.view())
            if decision.stop:
                final = ""
                for ev in reversed(self.log.events):
                    if ev.kind == "assistant" and ev.payload.get("text"):
                        final = ev.payload["text"]
                        break
                return self._result(decision.status, decision.reason, final or decision.reason)

    def _result(self, status: str, reason: str, final: str) -> RunResult:
        return RunResult(
            status=status,
            reason=reason,
            final=final,
            steps=self.steps,
            tool_calls=self.tool_calls,
            prompt_chars=self.prompt_chars,
            log=self.log,
            peak_prompt_chars=self.peak_prompt_chars,
            metrics={"est_tokens_in": math.ceil(self.prompt_chars / 4)},
        )


# ---------------------------------------------------------------------------
# 6. Controllers = workflows. Same kernel, different control flow.
# ---------------------------------------------------------------------------


def react(h: Harness, success: Callable[[Harness], Optional[str]]) -> RunResult:
    """L3: model decides, tools act, an external predicate decides success."""

    def stop(view: View) -> StopDecision:
        why = success(h)
        return StopDecision(stop=bool(why), reason=why or "", status="ok")

    return h.run(stop)


def plan_execute_verify(
    h: Harness,
    planner: Policy,
    verifier: Policy,
    success: Callable[[Harness], Optional[str]],
    max_replans: int = 1,
) -> RunResult:
    """L4: an explicit artifact (the plan) plus an independent verifier.

    Planner runs once with no tools (thinking only). Executor is `h.policy`.
    Verifier runs with no tools and only sees the transcript + success probe.
    """
    h.assemble()
    plan_action = planner.decide(h.view())
    h.log.append("note", text=f"PLAN:\n{plan_action.final}")
    h.steps += 1

    for attempt in range(max_replans + 1):
        res = react(h, success)
        if res.status == "ok" and success(h):
            return res
        verdict = verifier.decide(h.view())
        h.log.append("note", text=f"VERDICT (attempt {attempt + 1}): {verdict.final}")
        h.steps += 1
        if attempt == max_replans:
            return RunResult(
                status="failed",
                reason="verifier_rejected",
                final=res.final,
                steps=h.steps,
                tool_calls=h.tool_calls,
                prompt_chars=h.prompt_chars,
                log=h.log,
                peak_prompt_chars=h.peak_prompt_chars,
                metrics={"verdict": (verdict.final or "")[:200]},
            )
        # Re-plan: the artifact, not the whole history, carries the intent.
        h.log.append("user", text=f"Verifier rejected the result: {verdict.final}\nRevise and retry.")
    raise AssertionError("unreachable")


def orchestrator_workers(
    h: Harness,
    items: list[Any],
    make_worker: Callable[[Any], Harness],
    merge: Callable[[list[RunResult]], str],
) -> RunResult:
    """L5: fan-out with isolated contexts; only final text comes back."""
    sub: list[RunResult] = []
    for item in items:
        w = make_worker(item)
        sub.append(w.run(lambda _v: StopDecision(stop=True, reason="worker_done")))
        h.tool_calls += w.tool_calls
        h.prompt_chars += w.prompt_chars
    merged = merge(sub)
    h.log.append("note", text=f"WORKERS: {len(sub)}")
    h.log.append("assistant", text=merged)
    return RunResult(
        status="ok",
        reason="merged",
        final=merged,
        steps=h.steps + len(sub),
        tool_calls=h.tool_calls,
        prompt_chars=h.prompt_chars,
        log=h.log,
        peak_prompt_chars=max([r.peak_prompt_chars for r in sub] or [0]),
        metrics={"workers": len(sub), "worker_steps": [r.steps for r in sub]},
    )


def route(h: Harness, classify: Policy, flows: dict[str, Callable[[Harness], RunResult]]) -> RunResult:
    """L2: cheap classifier picks a bounded flow, and narrows the tool surface."""
    action = classify.decide(h.view())
    label = (action.final or "").strip().split()[0].lower()
    h.steps += 1
    h.log.append("note", text=f"ROUTE: {label}")
    flow = flows.get(label)
    if flow is None:
        h.log.append("note", text=f"no flow for '{label}'; escalating")
        return RunResult("failed", "unrouted", "", h.steps, h.tool_calls, h.prompt_chars, h.log)
    return flow(h)
