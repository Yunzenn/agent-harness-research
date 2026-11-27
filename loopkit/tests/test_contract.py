"""Contract checks: each test is one real failure mode from the stasis experiment."""

from __future__ import annotations

import unittest

from loopkit.contract import Contract, ContractMonitor


def tickets_world(resolved: int = 0) -> dict:
    return {"resolved": resolved, "total": 6}


CONTRACT = Contract(
    name="resolve tickets",
    done_when=lambda w: w["resolved"] == w["total"],
    advances=lambda w: w["resolved"] / w["total"],
    never=lambda name, args, w: "already resolved" if name == "set" and args.get("status") == "open" and w["resolved"] else None,
    stall_steps=3,
)


class TestPrematureStop(unittest.TestCase):
    def test_blocks_stop_when_goal_not_reached(self):
        m = ContractMonitor(CONTRACT)
        m.observe(value=0.67)
        v = m.before_stop(done=False)
        self.assertIsNotNone(v)
        self.assertEqual(v.kind, "premature_stop")

    def test_allows_stop_when_goal_reached(self):
        m = ContractMonitor(CONTRACT)
        m.observe(value=1.0)
        self.assertIsNone(m.before_stop(done=True))


class TestDrift(unittest.TestCase):
    def test_flags_when_progress_is_flat(self):
        m = ContractMonitor(CONTRACT)
        self.assertIsNone(m.observe(value=0.0))
        self.assertIsNone(m.observe(value=0.0))
        v = m.observe(value=0.0)
        self.assertIsNotNone(v)
        self.assertEqual(v.kind, "drift")
        self.assertIn("0.00", str(v))

    def test_does_not_flag_when_progress_rises(self):
        m = ContractMonitor(CONTRACT)
        for i in range(6):
            self.assertIsNone(m.observe(value=i / 6))

    def test_three_flat_observations_flag_even_after_a_gain(self):
        m = ContractMonitor(CONTRACT)
        m.observe(value=0.5)
        self.assertIsNone(m.observe(value=0.67))
        self.assertIsNone(m.observe(value=0.67))  # two flat observations: not yet
        v = m.observe(value=0.67)  # third flat observation: drift
        self.assertIsNotNone(v)
        self.assertEqual(v.kind, "drift")

    def test_reports_once_per_kind(self):
        m = ContractMonitor(CONTRACT)
        for _ in range(6):
            m.observe(value=0.0)
        self.assertEqual(len([v for v in m.violations if v.kind == "drift"]), 1)


class TestChurn(unittest.TestCase):
    def test_forbidden_call(self):
        m = ContractMonitor(CONTRACT)
        m.observe(value=1.0)
        v = m.before_call("set", {"status": "open"}, tickets_world(resolved=2))
        self.assertIsNotNone(v)
        self.assertEqual(v.kind, "churn")

    def test_allowed_call(self):
        m = ContractMonitor(CONTRACT)
        self.assertIsNone(m.before_call("get", {"tid": "T1"}, tickets_world(resolved=0)))


class TestTolerance(unittest.TestCase):
    def test_float_noise_still_counts_as_flat(self):
        m = ContractMonitor(CONTRACT)
        m.observe(value=0.5)
        m.observe(value=0.5 + 1e-12)
        self.assertIsNotNone(m.observe(value=0.5 - 1e-12))  # 1e-12 is noise, not progress


if __name__ == "__main__":
    unittest.main()

