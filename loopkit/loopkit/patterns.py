"""Controllers: the same kernel, different control flow.

Each function is a workflow you can name in a design review. They compose:
`route(...)` picks a `fanout(...)`, which builds workers that run `react(...)`.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

from .kernel import Harness
from .types import RunResult, Stop


# ---------------------------------------------------------------------------
# L3: bounded ReAct
# ---------------------------------------------------------------------------
def react(h: Harness, done: Callable[[Harness], Optional[str]]) -> RunResult:
    """Loop until an external predicate returns a truthy reason."""

    def stop(hh: Harness) -> Stop:
        why = done(hh)
        return Stop(stop=bool(why), reason=why or "")

    return h.run(stop)


# ---------------------------------------------------------------------------
# L1: fixed chain with a programmatic gate per stage
# ---------------------------------------------------------------------------
@dataclass
class Stage:
    name: str
    verify: Callable[[Harness], Optional[str]]  # returns reason when the stage passed
    allow: Optional[Iterable[str]] = None  # tool surface for this stage


def chain(h: Harness, stages: list[Stage]) -> RunResult:
    base_allow = h.tools.allow
    last: Optional[RunResult] = None
    try:
        for st in stages:
            h.tools.restrict(st.allow if st.allow is not None else base_allow)
            last = react(h, st.verify)
            h.log.append("note", text=f"stage '{st.name}' -> {last.status}/{last.reason}")
            if last.status == "budget":
                return last
    finally:
        h.tools.restrict(base_allow)
    assert last is not None
    return last


# ---------------------------------------------------------------------------
# L2: route first, then a narrow, bounded flow
# ---------------------------------------------------------------------------
@dataclass
class Lane:
    name: str
    allow: Optional[Iterable[str]]
    done: Callable[[Harness], Optional[str]]
    on_enter: Optional[Callable[[Harness], None]] = None
    max_steps: Optional[int] = None


def route(
    h: Harness,
    classify: Callable[[Harness], str],
    lanes: dict[str, Lane],
    fallback: str = "__escalate__",
) -> RunResult:
    started = time.monotonic()
    label = classify(h)
    h.steps += 1
    h.log.append("note", text=f"route -> {label}")
    lane = lanes.get(label)
    if lane is None:
        h.log.append("note", text=f"no lane for '{label}'; escalating (fallback={fallback})")
        return RunResult("failed", "unrouted", "", h.steps, h.tool_call_count, h.tokens_in, h.tokens_out, time.monotonic() - started, h.log)

    prev_allow, prev_budget = h.tools.allow, h.budget.max_steps
    if lane.max_steps is not None:
        h.budget.max_steps = h.steps + lane.max_steps
    try:
        if lane.on_enter:
            lane.on_enter(h)
        h.tools.restrict(lane.allow)
        res = react(h, lane.done)
    finally:
        h.tools.restrict(prev_allow)
        h.budget.max_steps = prev_budget
    res.metrics["lane"] = lane.name
    return res


# ---------------------------------------------------------------------------
# L5: orchestrator-workers with isolated contexts
# ---------------------------------------------------------------------------
@dataclass
class WorkerResult:
    item: Any
    text: str
    steps: int
    tokens_in: int
    wall_s: float
    error: Optional[str] = None


def fanout(
    parent: Harness,
    items: Iterable[Any],
    spawn: Callable[[Any], Harness],
    task: Callable[[Any], str],
    done: Callable[[Harness, Any], Optional[str]],
    merge: Callable[[list[WorkerResult]], str],
    max_workers: int = 6,
) -> RunResult:
    """Each worker gets a fresh log: it never sees the parent's context.

    Only the worker's final text crosses back. That is the whole point: the
    parent's context stays small no matter how much each worker read.
    """
    started = time.monotonic()
    items = list(items)

    def one(item: Any) -> WorkerResult:
        w = spawn(item)
        w.log.append("user", text=task(item))
        before_tokens = w.tokens_in
        res = w.run(lambda hh: Stop(stop=bool(done(hh, item)), reason=done(hh, item) or ""))
        return WorkerResult(item, res.text, res.steps, res.tokens_in - before_tokens, res.wall_s, None if res.status == "ok" else res.status)

    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(items) or 1))) as pool:
        outs = list(pool.map(one, items))

    merged = merge(outs)
    parent.steps += sum(o.steps for o in outs)
    parent.tokens_in += sum(o.tokens_in for o in outs)
    parent.log.append("note", text=f"fanout: {len(outs)} workers, {max_workers} lanes")
    parent.log.append("assistant_text", text=merged)
    return RunResult(
        "ok",
        "merged",
        merged,
        parent.steps,
        0,
        parent.tokens_in,
        parent.tokens_out,
        time.monotonic() - started,
        parent.log,
        {
            "workers": len(outs),
            "worker_steps": [o.steps for o in outs],
            "worker_tokens": sum(o.tokens_in for o in outs),
            "max_worker_tokens": max([o.tokens_in for o in outs], default=0),
            "failed_workers": sum(1 for o in outs if o.error),
        },
    )


# ---------------------------------------------------------------------------
# L4: plan -> execute -> verify, with a bounded number of re-plans
# ---------------------------------------------------------------------------
def plan_execute_verify(
    executor: Harness,
    planner: Harness,
    done: Callable[[Harness], Optional[str]],
    verify: Callable[[Harness], tuple[bool, str]],
    max_replans: int = 1,
) -> RunResult:
    planner.log.append("user", text=(executor.log.last_text() or "Plan the task.") + "\n\nReturn a numbered plan only.")
    plan = planner.run(lambda h: Stop(stop=True, reason="plan_ready"))
    executor.log.append("note", text="PLAN:\n" + plan.text)

    res = react(executor, done)
    for attempt in range(max_replans):
        if res.status == "ok" and done(executor):
            return res
        ok, feedback = verify(executor)
        executor.log.append("note", text=f"VERDICT {attempt + 1}: {feedback}")
        if ok:
            return res
        executor.log.append("user", text=f"Verifier rejected the result: {feedback}\nRevise and retry.")
        res = react(executor, done)

    ok, feedback = verify(executor)
    if not ok:
        res.status = "failed"
        res.reason = "verifier_rejected"
        res.metrics["verdict"] = feedback
    return res


# ---------------------------------------------------------------------------
# Human gate: an irreversible action becomes a state, not an error
# ---------------------------------------------------------------------------
def human_gate(
    h: Harness,
    tools: Iterable[str],
    approve: Optional[Callable[[str, dict], bool]] = None,
) -> None:
    """Install a guard that turns denied irreversible actions into a pending gate."""

    def guard(tool, args):
        if tool.name not in set(tools):
            return None
        if approve and approve(tool.name, args):
            return None
        h.request_human(tool.name, f"{tool.name} requires human approval", {"args": args})
        return f"{tool.name} requires human approval"

    h.tools.guards.append(guard)
