"""loopkit — 一个能按场景选拓扑、并真的更快的最小 agent harness。

    from loopkit import Brief, advise
    plan = advise(Brief(oracle="code", blast_radius="write", fanout=4))
    print(plan.explain())

The public surface is deliberately small:

  Brief / Plan / advise     场景 -> 拓扑（规则引擎，可解释）
  Harness / Budget          循环内核（日志、预算、工具批次）
  Registry / Tool           工具（校验、守卫、缓存、去重、并行）
  patterns.*                react / chain / route / fanout / plan_execute_verify
  MockProvider / Tiered     离线可测的模型层与模型分层
"""

from .advisor import Brief, Plan, Runnable, advise
from .contract import Contract, ContractMonitor, Violation
from .kernel import Harness
from .llm import MockProvider, OpenAICompatProvider, Tiered
from .patterns import Lane, Stage, WorkerResult, chain, fanout, human_gate, plan_execute_verify, react, route
from .session import SessionLog
from .tools import Param, Registry, Tool, ToolError
from .types import Action, Budget, RunResult, Stop, ToolCall, ToolResult
from .upgrade import GuardConfig, LoopGuard, LoopStats, ToolBox, ToolDenied

__version__ = "0.1.0"

__all__ = [
    "Action",
    "Brief",
    "Budget",
    "Contract",
    "ContractMonitor",
    "GuardConfig",
    "Harness",
    "Lane",
    "LoopGuard",
    "LoopStats",
    "MockProvider",
    "OpenAICompatProvider",
    "Param",
    "Plan",
    "Registry",
    "RunResult",
    "Runnable",
    "SessionLog",
    "Stage",
    "Stop",
    "Tiered",
    "Tool",
    "ToolBox",
    "ToolCall",
    "ToolDenied",
    "ToolError",
    "ToolResult",
    "Violation",
    "WorkerResult",
    "advise",
    "chain",
    "fanout",
    "human_gate",
    "plan_execute_verify",
    "react",
    "route",
]
