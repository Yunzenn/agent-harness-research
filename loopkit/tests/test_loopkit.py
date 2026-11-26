"""Structure tests: the invariants from the notes, executable."""

from __future__ import annotations

import time
import unittest

from loopkit import (
    Action,
    Brief,
    Budget,
    Harness,
    Lane,
    MockProvider,
    Param,
    Registry,
    SessionLog,
    Tool,
    ToolCall,
    advise,
    fanout,
    react,
    route,
)
from loopkit.llm import OpenAICompatProvider


def make_registry(tmp=None, cache_ttl=0.0, sleep=0.0):
    reg = Registry()

    def echo(text: str) -> str:
        if sleep:
            time.sleep(sleep)
        return text

    def slow_read(path: str) -> str:
        if sleep:
            time.sleep(sleep)
        return f"contents of {path}"

    reg.register(Tool("echo", "Echo text back.", {"text": Param("string")}, echo, cache_ttl_s=cache_ttl))
    reg.register(Tool("read", "Read a file.", {"path": Param("string")}, slow_read, cache_ttl_s=cache_ttl))
    return reg


def steps_from(messages) -> int:
    return sum(1 for m in messages if m.get("role") == "assistant")


class TestKernel(unittest.TestCase):
    def test_every_call_gets_exactly_one_result(self):
        def brain(req):
            n = steps_from(req.messages)
            if n == 0:
                return Action(calls=[ToolCall("echo", {"text": "a"}), ToolCall("echo", {"text": "b"})])
            return Action(text="done")

        h = Harness(MockProvider(brain), make_registry(), Budget(max_steps=5))
        h.log.append("user", text="go")
        res = react(h, lambda hh: "ok" if hh.log.last_text() == "done" else None)

        calls = [e for e in h.log.events if e.kind == "assistant_calls"]
        results = [e for e in h.log.events if e.kind == "tool_result"]
        called = [c["id"] for e in calls for c in e.data["calls"]]
        answered = [e.data["call_id"] for e in results]
        self.assertEqual(called, answered)
        self.assertEqual(res.status, "ok")
        # the projection must be a valid assistant/tool sequence
        msgs = h.log.messages()
        self.assertEqual(msgs[1]["role"], "assistant")
        self.assertEqual(len(msgs[1]["tool_calls"]), 2)
        self.assertEqual(msgs[2]["role"], "tool")

    def test_budget_stops_the_loop(self):
        def brain(req):
            return Action(calls=[ToolCall("echo", {"text": "x"})])

        h = Harness(MockProvider(brain), make_registry(), Budget(max_steps=3, max_tool_calls=99))
        h.log.append("user", text="spin")
        res = react(h, lambda hh: None)
        self.assertEqual(res.status, "budget")
        self.assertEqual(res.reason, "max_steps")
        self.assertEqual(res.steps, 3)

    def test_tool_failure_is_data_not_exception(self):
        reg = Registry()

        def boom() -> str:
            raise RuntimeError("kaboom")

        reg.register(Tool("boom", "Always fails.", {}, boom))

        def brain(req):
            if steps_from(req.messages) == 0:
                return Action(calls=[ToolCall("boom", {})])
            return Action(text="recovered")

        h = Harness(MockProvider(brain), reg, Budget(max_steps=3))
        h.log.append("user", text="go")
        res = react(h, lambda hh: "ok" if hh.log.last_text() == "recovered" else None)
        fail = [e for e in h.log.events if e.kind == "tool_result"][0]
        self.assertFalse(fail.data["ok"])
        self.assertEqual(fail.data["code"], "TOOL_CRASHED")
        self.assertEqual(res.status, "ok")


class TestSpeedMechanisms(unittest.TestCase):
    def test_cache_avoids_second_execution(self):
        reg = make_registry(cache_ttl=60.0)

        def brain(req):
            if steps_from(req.messages) < 2:
                return Action(calls=[ToolCall("read", {"path": "a.txt"})])
            return Action(text="done")

        h = Harness(MockProvider(brain), reg, Budget(max_steps=5))
        h.log.append("user", text="go")
        react(h, lambda hh: "ok" if hh.log.last_text() == "done" else None)
        self.assertEqual(reg.stats.executed, 1)
        self.assertEqual(reg.stats.cache_hits, 1)

    def test_duplicate_calls_in_one_batch_run_once(self):
        reg = make_registry()

        def brain(req):
            if steps_from(req.messages) == 0:
                return Action(calls=[ToolCall("read", {"path": "x"}), ToolCall("read", {"path": "x"})])
            return Action(text="done")

        h = Harness(MockProvider(brain), reg, Budget(max_steps=4))
        h.log.append("user", text="go")
        react(h, lambda hh: "ok" if hh.log.last_text() == "done" else None)
        self.assertEqual(reg.stats.deduped, 1)
        self.assertEqual(reg.stats.executed, 1)

    def test_parallel_batch_is_faster_and_ordered(self):
        reg = make_registry(sleep=0.08)

        def brain(req):
            if steps_from(req.messages) == 0:
                return Action(calls=[ToolCall("read", {"path": str(i)}) for i in range(4)])
            return Action(text="done")

        h = Harness(MockProvider(brain), reg, Budget(max_steps=4))
        h.log.append("user", text="go")
        t0 = time.monotonic()
        react(h, lambda hh: "ok" if hh.log.last_text() == "done" else None)
        wall = time.monotonic() - t0
        self.assertLess(wall, 0.28, "4 x 80ms should overlap, not serialize")
        results = [e.data["content"] for e in h.log.events if e.kind == "tool_result"]
        self.assertEqual(results, [f"contents of {i}" for i in range(4)])
        self.assertEqual(reg.stats.parallel_batches, 1)

    def test_irreversible_call_is_a_barrier(self):
        reg = make_registry()

        def write() -> str:
            time.sleep(0.05)
            return "written"

        reg.register(Tool("write", "Write.", {}, write, read_only=False, parallel_safe=False, destructive=True))

        def brain(req):
            if steps_from(req.messages) == 0:
                return Action(calls=[ToolCall("read", {"path": "1"}), ToolCall("write", {}), ToolCall("read", {"path": "2"})])
            return Action(text="done")

        h = Harness(MockProvider(brain), reg, Budget(max_steps=4))
        h.log.append("user", text="go")
        react(h, lambda hh: "ok" if hh.log.last_text() == "done" else None)
        results = [e.data["name"] for e in h.log.events if e.kind == "tool_result"]
        self.assertEqual(results, ["read", "write", "read"])


class TestPatterns(unittest.TestCase):
    def test_route_without_lane_escalates(self):
        def classify(h):
            return "refund"

        def brain(req):
            return Action(text="ok")

        h = Harness(MockProvider(brain), make_registry(), Budget(max_steps=3))
        h.log.append("user", text="refund me")
        res = route(h, classify, {"billing": Lane("billing", None, lambda hh: "done")})
        self.assertEqual(res.status, "failed")
        self.assertEqual(res.reason, "unrouted")

    def test_route_narrows_tools(self):
        seen = {}

        def brain(req):
            seen["tools"] = [t["function"]["name"] for t in req.tools]
            return Action(text="done")

        h = Harness(MockProvider(brain), make_registry(), Budget(max_steps=3))
        h.log.append("user", text="x")
        lane = Lane("billing", ["echo"], lambda hh: "done")
        route(h, lambda hh: "billing", {"billing": lane})
        self.assertEqual(seen["tools"], ["echo"])

    def test_fanout_isolates_contexts(self):
        reg = make_registry()

        def worker_brain(req):
            return Action(text="claim-" + str(len(req.messages)))

        parent = Harness(MockProvider(lambda r: Action(text="")), reg, Budget(max_steps=2))
        parent.log.append("user", text="summarise 6 docs")
        res = fanout(
            parent,
            list(range(6)),
            spawn=lambda item: Harness(MockProvider(worker_brain), reg, Budget(max_steps=2)),
            task=lambda item: f"read doc {item}",
            done=lambda hh, item: "ok" if hh.log.last_text() else None,
            merge=lambda outs: "; ".join(o.text for o in outs),
            max_workers=3,
        )
        self.assertEqual(res.metrics["workers"], 6)
        # the parent never accumulated the workers' transcripts
        self.assertLess(len(parent.log), 6)
        self.assertIn("claim", res.text)


class TestAdvisor(unittest.TestCase):
    def test_human_oracle_becomes_a_chain_with_signoff(self):
        plan = advise(Brief(goal="grade essays", oracle="human", blast_radius="read"))
        self.assertEqual(plan.pattern, "chain")
        self.assertIn("human: 产出必须经人确认才算完成", plan.gates)

    def test_unknown_subtasks_fan_out(self):
        plan = advise(Brief(goal="migrate repo", oracle="code", subtasks_known=False, fanout=12))
        self.assertEqual(plan.pattern, "fanout")
        self.assertEqual(plan.concurrency, 8)
        self.assertTrue(any("L6" in w for w in plan.warnings))

    def test_irreversible_needs_approval_gate(self):
        plan = advise(Brief(goal="refund", oracle="rubric", blast_radius="irreversible"))
        self.assertTrue(any(g.startswith("approval") for g in plan.gates))
        self.assertEqual(plan.tool_policy, "narrow")

    def test_durable_run_gets_checkpoint_and_l7(self):
        plan = advise(Brief(goal="on-call bot", oracle="code", lifetime="days"))
        self.assertEqual(plan.topology, "L7")
        self.assertTrue(any(g.startswith("checkpoint") for g in plan.gates))

    def test_no_oracle_warns_loudly(self):
        plan = advise(Brief(goal="write strategy", oracle="none"))
        self.assertTrue(any("没有判据" in w for w in plan.warnings))

    def test_build_names_missing_parts(self):
        plan = advise(Brief(goal="fix bug", oracle="code"))
        with self.assertRaises(ValueError) as ctx:
            plan.build(provider=MockProvider(lambda r: Action(text="x")), registry=make_registry())
        self.assertIn("done", str(ctx.exception))

    def test_built_plan_runs(self):
        plan = advise(Brief(goal="fix bug", oracle="code"))
        self.assertEqual(plan.pattern, "plan_verify")

        def brain(req):
            if steps_from(req.messages) == 0:
                return Action(calls=[ToolCall("echo", {"text": "fixed"})])
            return Action(text="fixed")

        runnable = plan.build(
            provider=MockProvider(brain),
            registry=make_registry(),
            planner=MockProvider(lambda req: Action(text="1) echo 2) verify")),
            done=lambda hh: "ok" if hh.log.last_text() == "fixed" else None,
            verify=lambda hh: (True, "looks fine"),
        )
        res = runnable.run("fix the bug")
        self.assertEqual(res.status, "ok")


class TestProviderShaping(unittest.TestCase):
    def test_payload_and_tool_call_parsing(self):
        captured = {}

        def transport(url, headers, payload):
            captured["url"] = url
            captured["payload"] = payload
            return {
                "model": "test-model",
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {"id": "t1", "function": {"name": "echo", "arguments": '{"text": "hi"}'}}
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 11, "completion_tokens": 5},
            }

        p = OpenAICompatProvider("https://example.test/v1", "k", "test-model", transport=transport)
        from loopkit.types import LLMRequest

        resp = p.complete(LLMRequest(system="sys", messages=[{"role": "user", "content": "hi"}], tools=[], model="test-model"))
        self.assertEqual(captured["url"], "https://example.test/v1/chat/completions")
        self.assertEqual(captured["payload"]["messages"][0], {"role": "system", "content": "sys"})
        self.assertEqual(resp.tool_calls[0].name, "echo")
        self.assertEqual(resp.tool_calls[0].args, {"text": "hi"})
        self.assertEqual((resp.tokens_in, resp.tokens_out), (11, 5))

    def test_tiered_routes_by_model_prefix(self):
        from loopkit import MockProvider, Tiered
        from loopkit.types import LLMRequest

        small = MockProvider(lambda r: Action(text="small"), name="small")
        large = MockProvider(lambda r: Action(text="large"), name="large")
        t = Tiered({"router": small, "exec": large})
        ask = lambda model: t.complete(LLMRequest(system="", messages=[], tools=[], model=model)).text
        self.assertEqual(ask("router-1"), "small")
        self.assertEqual(ask("exec-1"), "large")
        self.assertEqual(ask("unknown"), "large")  # falls back to the last tier


class TestDurability(unittest.TestCase):
    def test_checkpoint_roundtrip(self):
        log = SessionLog()
        log.append("user", text="hello")
        log.append("note", text="internal")
        snap = log.checkpoint()
        restored = SessionLog.restore(snap)
        self.assertEqual(restored.messages(), log.messages())
        self.assertEqual(restored.transcript(), log.transcript())


if __name__ == "__main__":
    unittest.main()
