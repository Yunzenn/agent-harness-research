"""Tests for the upgrade layer: guard + toolbox wrapped around a foreign loop."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest

from loopkit.upgrade import GuardConfig, LoopGuard, ToolBox, ToolDenied


class TestGuard(unittest.TestCase):
    def test_step_budget_stops(self):
        g = LoopGuard(GuardConfig(max_steps=3))
        self.assertIsNone(g.begin_step())
        self.assertIsNone(g.begin_step())
        self.assertIsNone(g.begin_step())
        self.assertEqual(g.begin_step(), "max_steps")
        self.assertEqual(g.finish().stop_reason, "max_steps")

    def test_repeat_denied_after_threshold(self):
        g = LoopGuard(GuardConfig(max_repeats=2))
        self.assertIsNone(g.allow("read", {"path": "a"}))
        self.assertIsNone(g.allow("read", {"path": "a"}))
        reason = g.allow("read", {"path": "a"})
        self.assertIsNotNone(reason)
        self.assertIn("repeated", reason)
        self.assertEqual(g.stats.repeats, 1)

    def test_tool_budget_denies_even_without_step_boundary(self):
        g = LoopGuard(GuardConfig(max_tool_calls=2, max_repeats=0))
        for _ in range(2):
            self.assertIsNone(g.allow("t", {}))
            g.record("t", True, 0.0)
        reason = g.allow("t", {})
        self.assertIn("budget exhausted", reason)
        self.assertEqual(g.stats.stop_reason, "max_tool_calls")

    def test_approval_required(self):
        g = LoopGuard(GuardConfig(require_approval=("refund",)))
        self.assertIn("approval", g.allow("refund", {"amount": 1}))

    def test_trace_file(self):
        g = LoopGuard(GuardConfig(max_steps=2))
        g.begin_step()
        g.begin_step()
        g.begin_step()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "trace.jsonl")
            g.trace(path)
            with open(path, encoding="utf-8") as fh:
                lines = [json.loads(x) for x in fh if x.strip()]
        self.assertTrue(any(e["event"] == "stop" for e in lines))


class TestToolBox(unittest.TestCase):
    def setUp(self):
        self.calls = {"n": 0}

        def read(path: str) -> str:
            self.calls["n"] += 1
            time.sleep(0.001)
            return "content:" + path

        self.box = ToolBox({"read": read}, guard=LoopGuard(GuardConfig(max_repeats=0)), cache_ttl_s=60)

    def test_cache_hit_executes_once(self):
        a = self.box.call("read", {"path": "a"})
        b = self.box.call("read", {"path": "a"})
        self.assertEqual(a, b)
        self.assertEqual(self.calls["n"], 1)
        self.assertEqual(self.box.stats().cache_hits, 1)

    def test_denial_raises_with_reason(self):
        box = ToolBox({"read": lambda path: path}, guard=LoopGuard(GuardConfig(max_repeats=1)), cache_ttl_s=0)
        box.call("read", {"path": "x"})
        with self.assertRaises(ToolDenied) as ctx:
            box.call("read", {"path": "x"})
        self.assertIn("repeated", ctx.exception.reason)

    def test_batch_dedupes_and_preserves_order(self):
        box = ToolBox({"read": lambda path: "content:" + path}, cache_ttl_s=60)
        out = box.batch([("read", {"path": "a"}), ("read", {"path": "b"}), ("read", {"path": "a"})])
        self.assertEqual(out, ["content:a", "content:b", "content:a"])

    def test_batch_is_concurrent(self):
        def slow(x: int) -> int:
            time.sleep(0.08)
            return x * 2

        box = ToolBox({"slow": slow}, cache_ttl_s=0)
        t0 = time.monotonic()
        out = box.batch([("slow", {"x": i}) for i in range(4)])
        wall = time.monotonic() - t0
        self.assertEqual(out, [0, 2, 4, 6])
        self.assertLess(wall, 0.25)

    def test_unknown_tool(self):
        with self.assertRaises(KeyError):
            self.box.call("nope", {})


if __name__ == "__main__":
    unittest.main()
