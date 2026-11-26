"""Scenario -> plan. A rule engine, not a model.

You describe the *business* (can it be verified? is it reversible? does it wait on
people?), and this returns a topology, budgets, a tool policy, gates, and the
reasons it chose them. Every reason is a sentence you can argue with in review.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

HIGH_DENSITY = {"code", "render", "schema"}
LOW_DENSITY = {"rubric", "none", "human"}


@dataclass
class Brief:
    goal: str = ""
    oracle: str = "none"  # code | render | schema | rubric | human | none
    oracle_latency_s: float = 1.0
    reversible: bool = True
    blast_radius: str = "read"  # read | write | irreversible
    lifetime: str = "session"  # session | hours | days
    heterogeneity: str = "single"  # single | few | many
    subtasks_known: bool = True
    fanout: int = 1
    latency_budget_s: float = 60.0
    cost_budget_usd: float = 1.0
    notes: str = ""


@dataclass
class Plan:
    topology: str
    pattern: str
    concurrency: int
    tool_policy: str
    gates: list[str]
    budgets: dict[str, Any]
    tiers: dict[str, str]
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    brief: Optional[Brief] = None

    # -- presentation ------------------------------------------------------
    def explain(self) -> str:
        rows = [
            ("拓扑", f"{self.topology} / {self.pattern}"),
            ("并发", str(self.concurrency)),
            ("工具面", self.tool_policy),
            ("闸门", "; ".join(self.gates) or "—"),
            ("预算", ", ".join(f"{k}={v}" for k, v in self.budgets.items())),
            ("模型分层", ", ".join(f"{k}->{v}" for k, v in self.tiers.items())),
        ]
        width = max(len(r[0]) for r in rows)
        body = "\n".join(f"  {a.ljust(width)}  {b}" for a, b in rows)
        why = "\n".join(f"  - {r}" for r in self.reasons)
        warn = "\n".join(f"  ! {w}" for w in self.warnings)
        out = f"计划：{self.topology} / {self.pattern}\n{body}\n理由：\n{why}"
        return out + (f"\n注意：\n{warn}" if warn else "")

    def make_budget(self):
        from .types import Budget

        return Budget(
            max_steps=int(self.budgets["max_steps"]),
            max_tool_calls=int(self.budgets["max_tool_calls"]),
            max_tokens_in=int(self.budgets["max_tokens_in"]),
            max_seconds=float(self.budgets["max_seconds"]),
        )

    # -- execution ---------------------------------------------------------
    def build(self, **parts: Any):
        """Assemble the recommended workflow. Missing parts are named explicitly."""
        required = {
            "react": ["provider", "registry", "done"],
            "chain": ["provider", "registry", "stages"],
            "route": ["provider", "registry", "classify", "lanes"],
            "fanout": ["spawn", "items", "task_of", "done", "merge"],
            "plan_verify": ["provider", "registry", "planner", "done", "verify"],
        }[self.pattern]
        missing = [k for k in required if parts.get(k) is None]
        if missing:
            raise ValueError(f"pattern '{self.pattern}' needs: {', '.join(missing)}")
        return Runnable(self, parts)


class Runnable:
    def __init__(self, plan: Plan, parts: dict[str, Any]) -> None:
        self.plan = plan
        self.parts = parts

    def run(self, task: str):
        from .kernel import Harness
        from .patterns import chain, fanout, plan_execute_verify, react, route

        p, parts = self.plan, self.parts

        if p.pattern == "fanout":
            parent = Harness(parts.get("provider"), parts.get("registry"), self.plan.make_budget(), system=parts.get("system", ""))
            parent.log.append("user", text=task)
            return fanout(
                parent,
                parts["items"],
                parts["spawn"],
                parts["task_of"],
                parts["done"],
                parts["merge"],
                max_workers=p.concurrency,
            )

        h = Harness(parts["provider"], parts["registry"], self.plan.make_budget(), system=parts.get("system", ""))
        h.log.append("user", text=task)

        if p.pattern == "react":
            return react(h, parts["done"])
        if p.pattern == "chain":
            return chain(h, parts["stages"])
        if p.pattern == "route":
            return route(h, parts["classify"], parts["lanes"])
        if p.pattern == "plan_verify":
            planner = Harness(parts["planner"], parts["registry"], p.make_budget(), system=parts.get("system", ""))
            return plan_execute_verify(h, planner, parts["done"], parts["verify"])
        raise ValueError(f"unknown pattern {p.pattern}")


def advise(b: Brief) -> Plan:
    reasons: list[str] = []
    warnings: list[str] = []
    durable = b.lifetime in ("hours", "days")
    high = b.oracle in HIGH_DENSITY
    no_oracle = b.oracle in LOW_DENSITY

    # 1. control flow -------------------------------------------------------
    if not b.subtasks_known and b.fanout >= 3:
        pattern = "fanout"
        reasons.append(f"子任务数事先不可知且预计 ≥3（{b.fanout}），用编排者-工人扇出而不是写死流程")
    elif b.heterogeneity == "many":
        pattern = "route"
        reasons.append("需求分成多类且每类工具不同：先分类再进窄车道，避免一套工具打天下")
    elif no_oracle:
        pattern = "chain"
        reasons.append(f"判据在人的一侧（oracle={b.oracle}）：用带闸门的固定链，不让模型自动收敛")
    elif high and b.fanout >= 3:
        pattern = "fanout"
        reasons.append("判据可自动验证，且子任务彼此独立：分节并行，结果用同一契约汇总")
    elif high:
        pattern = "plan_verify"
        reasons.append("判据可自动验证且任务跨多步：先出计划工件，再做独立校验")
    else:
        pattern = "react"
        reasons.append("有可用判据、副作用可控：有界 ReAct 足够，最便宜")

    topology = {
        "chain": "L1",
        "route": "L2",
        "react": "L3",
        "plan_verify": "L4",
        "fanout": "L5",
    }[pattern]
    if durable:
        topology = "L7"
        reasons.append(f"寿命={b.lifetime}：拓扑之上必须叠持久化（检查点/幂等/唤醒）")

    # 2. concurrency --------------------------------------------------------
    if pattern == "fanout":
        concurrency = max(1, min(b.fanout, 8))
        if b.fanout > 8:
            warnings.append(f"扇出 {b.fanout} > 8：建议升级到 L6（脚本编排）并加分批，否则并发上限会变成瓶颈")
    elif pattern == "route":
        concurrency = max(1, min(b.fanout, 4))
        reasons.append(f"车道内并发 {concurrency}；每条车道有独立步数上限，防止单条失控")
    else:
        concurrency = 1

    # 3. tool policy --------------------------------------------------------
    if b.blast_radius == "read":
        tool_policy = "read-only"
        reasons.append("副作用面只有读：沙箱可以放到最宽，审批可以全关")
    elif b.heterogeneity == "many":
        tool_policy = "narrow-per-lane"
        reasons.append("每类需求只暴露它需要的 3–6 个工具：省 token，也减少选错")
    else:
        tool_policy = "narrow"
        reasons.append("写操作单独授权，读工具与写工具在 schema 层就分开")

    # 4. gates --------------------------------------------------------------
    gates: list[str] = []
    if b.blast_radius == "irreversible":
        gates.append("approval: 每个不可逆动作（代码路径，非提示）")
        reasons.append("存在不可逆副作用：闸门必须由控制层执行，且拒绝不可被提示词推翻")
    elif b.blast_radius == "write":
        gates.append("approval: 不可逆子集 + dry-run")
    if no_oracle:
        gates.append("human: 产出必须经人确认才算完成")
    if durable:
        gates.extend(["checkpoint: 每步落盘", "idempotency: 同一事件只处理一次"])
    if not b.reversible and b.blast_radius != "irreversible":
        warnings.append("标记为不可逆但 blast_radius 不是 irreversible：两者矛盾，请确认")

    # 5. budgets ------------------------------------------------------------
    steps = {"react": 8, "plan_verify": 14, "chain": 12, "route": 6, "fanout": 3}[pattern]
    max_calls = {"react": 24, "plan_verify": 40, "chain": 30, "route": 16, "fanout": 8}[pattern]
    if pattern == "fanout":
        max_calls = max_calls * concurrency
    tokens = int(max(20_000, min(2_000_000, b.cost_budget_usd * 200_000)))
    budgets = {
        "max_steps": steps,
        "max_tool_calls": max_calls,
        "max_tokens_in": tokens,
        "max_seconds": round(b.latency_budget_s, 1),
    }
    if b.cost_budget_usd < 0.05:
        warnings.append("成本预算 < $0.05：几乎只够单发调用，任何循环都会超支")

    # 6. model tiers --------------------------------------------------------
    tiers: dict[str, str] = {}
    if b.heterogeneity == "many":
        tiers["router"] = "small"
        tiers["exec"] = "large"
        reasons.append("分类用便宜模型（20–50ms 级），执行用强模型：路由不该按强模型计费")
    elif pattern == "fanout":
        tiers["worker"] = "small"
        tiers["merge"] = "large"
        reasons.append("工人只做抽取/摘要，合并再上强模型")
    else:
        tiers["exec"] = "large"

    # 7. sanity warnings ----------------------------------------------------
    if b.oracle == "none":
        warnings.append("没有判据：不要指望模型自评收敛，每次运行都必须以人的确认结束")
    if b.oracle in LOW_DENSITY and b.fanout > 4:
        warnings.append("低判据密度 + 大扇出：错误会被并行放大，建议先小规模验证判据")
    if b.latency_budget_s < 10 and pattern in ("plan_verify", "fanout"):
        warnings.append("延迟预算 < 10s 与所选拓扑冲突：多阶段必然超时")

    return Plan(topology, pattern, concurrency, tool_policy, gates, budgets, tiers, reasons, warnings, b)
