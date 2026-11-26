"""Runs the five scenarios and prints traces + a cost table.

    python run_all.py            # traces + table
    python run_all.py --quiet    # table only
"""

from __future__ import annotations

import sys
from textwrap import shorten

import scenarios as S
from kernel import RunResult


def show(res: RunResult, title: str, limit: int = 40) -> None:
    print(f"\n=== {title} ===")
    print(res.log.transcript() if limit is None else shorten(res.log.transcript(), 4000, placeholder=" ..."))
    print(f"--> {res.summary()}")


def main() -> None:
    quiet = "--quiet" in sys.argv
    rows: list[tuple[str, str, RunResult | None]] = []
    checks: list[tuple[str, bool, str]] = []

    # S1 -------------------------------------------------------------------
    r1, w1 = S.s1_bugfix()
    if not quiet:
        show(r1, "S1 bugfix — free loop, hard judge (L3)")
    rows.append(("S1 bugfix", "ReAct + external judge", r1))
    checks.append(("S1 the test suite is the stop condition", w1.tests_pass(), w1.files["calc.py"].splitlines()[-1]))
    checks.append(("S1 loop stopped for the right reason", r1.reason == "tests_pass", r1.reason))

    # S2 -------------------------------------------------------------------
    naive, verified = S.s2_prose()
    if not quiet:
        show(naive, "S2a prose — bare loop, finalize == success (the trap)")
        show(verified, "S2b prose — plan/execute/verify with a deterministic rubric (L4)")
    rows.append(("S2a prose", "ReAct (self-judged)", naive))
    rows.append(("S2b prose", "plan-execute-verify", verified))
    checks.append(("S2a naive run 'succeeds' with zero citations", S.cited(naive.final) is False, "0 citations"))
    checks.append(("S2b verified run cites every bullet", S.cited(verified.final) is True, "all cited"))

    # S3 -------------------------------------------------------------------
    single, fan = S.s3_research()
    if not quiet:
        print("\n=== S3 research — one context vs fan-out ===")
        print(f"single: {single.summary()}")
        print(f"fan-out: {fan.summary()}  workers={fan.metrics['workers']} steps={fan.metrics['worker_steps']}")
    rows.append(("S3a research", "single context", single))
    rows.append(("S3b research", "orchestrator-workers", fan))
    checks.append(
        (
            "S3 fan-out lowers the largest single context (peak)",
            fan.peak_prompt_chars < single.peak_prompt_chars,
            f"peak {fan.peak_prompt_chars} < {single.peak_prompt_chars}",
        )
    )
    checks.append(
        (
            "S3 fan-out pays for it in total tokens (isolation is not free)",
            fan.prompt_chars > single.prompt_chars,
            f"total {fan.prompt_chars} > {single.prompt_chars}",
        )
    )

    # S4 -------------------------------------------------------------------
    routed = S.s4_routing()
    if not quiet:
        print("\n=== S4 routing — narrow tools per lane ===")
        for tid, res in routed:
            print(f"{tid}: {res.summary()}  notes={[e.payload['text'] for e in res.log.events if e.kind == 'note']}")
            print(shorten(res.log.transcript().replace("\n", " | "), 400, placeholder=" ..."))
    for tid, res in routed:
        rows.append((f"S4 {tid}", "router + bounded flow", res))
    checks.append(("S4 the suspicious ticket is not routed into a refund lane", routed[2][1].reason == "unrouted", routed[2][1].reason))

    # S5 -------------------------------------------------------------------
    gated, resumed, w5 = S.s5_refund()
    if not quiet:
        show(gated, "S5a refund — irreversible action hits the gate")
        if resumed:
            show(resumed, "S5b refund — resumed from checkpoint after approval")
    rows.append(("S5a refund", "guard + human gate (L7)", gated))
    rows.append(("S5b refund", "resume from log", resumed))
    checks.append(("S5 nothing irreversible happened before approval", gated.status == "awaiting_human", gated.status))
    checks.append(("S5 the approved action executed exactly once", w5.refunded == ("T-1", 120), str(w5.refunded)))

    # table ----------------------------------------------------------------
    print("\n=== cost table (prompt_chars = cumulative re-sent context) ===")
    print(
        f"{'scenario':<14}{'pattern':<26}{'steps':>6}{'calls':>7}{'total_chars':>13}{'peak_chars':>12}"
        f"{'~tokens_in':>12}  outcome"
    )
    for name, pattern, res in rows:
        if res is None:
            continue
        print(
            f"{name:<14}{pattern:<26}{res.steps:>6}{res.tool_calls:>7}{res.prompt_chars:>13,}"
            f"{res.peak_prompt_chars:>12,}{round(res.prompt_chars/4):>12,}  {res.status}/{res.reason}"
        )

    print("\n=== checks ===")
    failed = 0
    for name, ok, detail in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}  ({detail})")
        failed += 0 if ok else 1
    print(f"\n{len(checks) - failed}/{len(checks)} checks passed")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
