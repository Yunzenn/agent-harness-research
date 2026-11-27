# Harness Lab

**给已有 agent loop 加装的一层：更少调用、更硬的停止、可回放的轨迹。**
*A hardening layer for loops you already have — fewer calls, hard stops, replayable traces.*

[中文](#中文) · [English](#english)

![tests](https://img.shields.io/badge/tests-30%20passing-brightgreen)
![python](https://img.shields.io/badge/python-3.10%2B-blue)
![node](https://img.shields.io/badge/node-20%2B-339933)
![license](https://img.shields.io/badge/license-MIT-yellow)

---

<a id="中文"></a>
## 中文

### 它做什么

不造框架。你的 while 循环 / LangGraph 图 / DSH 会话一行不改，外面包三个调用：

```python
from loopkit.upgrade import GuardConfig, LoopGuard, ToolBox, ToolDenied

guard = LoopGuard(GuardConfig(max_steps=12, max_tool_calls=20, cache_ttl_s=60))
box = ToolBox({"read": read_file, "run_tests": run_tests}, guard=guard)

while True:
    if guard.begin_step(tokens_in=usage):        # 预算/墙钟到了就停
        break
    try:
        out = box.call("read", {"path": "a.py"})  # 缓存 + 重复拦截
    except ToolDenied as exc:
        out = f"skipped: {exc.reason}"            # 拒绝是数据，不是崩溃

print(guard.finish().as_row())
```

| 能力 | 机制 | 可测指标 |
|---|---|---|
| **花得少** | 工具缓存、批内去重、有界并发 | 真实执行次数 ↓、墙钟 ↓ |
| **停得对** | 四本预算、重复调用拦截、破坏性操作审批 | 失控 loop 100% 在预算内停 |
| **看得见** | `LoopStats` + `trace.jsonl` | 每步/每调用可回放 |

### 实测：同一个 loop，只换调用层

工具耗时 40ms，同一段循环代码跑两遍（`bench/uplift_bench.py`）：

| 场景 | 裸循环执行 | 加装后 | 降幅 | 墙钟降幅 | 停止原因 |
|---|---:|---:|---:|---:|---|
| 12 个工单（重复读两遍） | 24 | 12 | **−50%** | −50% | loop_exit |
| 失控重试（永不收敛） | 50 | 12 | **−76%** | −76% | `max_tool_calls` |
| 同一调用重复 10 次 | 10 | 3 | **−70%** | −70% | loop_exit |

注意第三行：**不是把一次调用变快，而是拒绝执行第 4 次**。

### 为什么不是框架

框架要接管控制流，代价是迁移。我们只做两件事：**门**（预算/审批/重复）和**账**（指标/轨迹）。

判断标准：**去掉这一层，你的 loop 还能跑吗？** 能，才是加装。

### 仓库内容

| 路径 | 是什么 |
|---|---|
| `loopkit/upgrade.py` | **主线**：`LoopGuard` + `ToolBox` + 统计/轨迹 |
| `loopkit/`（kernel、patterns、advisor） | 参考实现：用来跑基准、验证"哪种拓扑更便宜" |
| `dsh-plugin/` | advisor 的 DSH 接入（下一步接 guard） |
| `01`–`05` 笔记 | 研究背景：循环不变量、场景→拓扑、工具/技能/MCP、失败与评估 |
| `docs/POSITIONING.md` | 定位、非目标、验收标准 |
| `experiments/stasis/` | 真实模型实验：24 条轨迹、三个检测器对比、契约层验证 |

### 不做

❌ 通用图引擎 · ❌ MCP/记忆/向量库 · ❌ 托管平台/UI · ❌ 多 agent 协议 · ❌ 流式取舍（宿主决定）

### 快速开始

```powershell
git clone https://github.com/Yunzenn/harness-lab.git
cd harness-lab
$env:PYTHONPATH="loopkit"

python -m unittest discover -s loopkit/tests -t loopkit   # 30 tests
python loopkit/bench/uplift_bench.py                      # 本页的实测表
python -m loopkit demo                                    # 参考实现：一个 kernel，四种控制流
```

### 局限

工具是模拟延迟；线程而非 asyncio；尚未发布 PyPI（`loopkit` 名字已被占用，将改名发布）；没有真实模型的数字。

---

<a id="english"></a>
## English

### What it does

No new framework. Keep your `while` loop, your LangGraph graph, your DSH session — wrap three calls around it:

```python
from loopkit.upgrade import GuardConfig, LoopGuard, ToolBox, ToolDenied

guard = LoopGuard(GuardConfig(max_steps=12, max_tool_calls=20, cache_ttl_s=60))
box = ToolBox({"read": read_file, "run_tests": run_tests}, guard=guard)

while True:
    if guard.begin_step(tokens_in=usage):
        break
    try:
        out = box.call("read", {"path": "a.py"})
    except ToolDenied as exc:
        out = f"skipped: {exc.reason}"

print(guard.finish().as_row())
```

| Capability | Mechanism | Measurable |
|---|---|---|
| **Spend less** | tool cache, batch de-dup, bounded concurrency | real executions ↓, wall clock ↓ |
| **Stop right** | four budgets, repeat interception, approval gates | runaway loops stop 100% |
| **See it** | `LoopStats` + `trace.jsonl` | every step replayable |

### Measured: same loop, only the call layer swapped

40 ms tools, the same loop body run twice (`bench/uplift_bench.py`):

| Scenario | Bare executions | Wrapped | Δ | Wall Δ | Stop |
|---|---:|---:|---:|---:|---|
| 12 tickets (read twice) | 24 | 12 | **−50%** | −50% | loop_exit |
| Runaway retry (never converges) | 50 | 12 | **−76%** | −76% | `max_tool_calls` |
| Same call x10 | 10 | 3 | **−70%** | −70% | loop_exit |

The third row is the point: it does not make a call faster, it **refuses the fourth one**.

### Why not a framework

A framework takes over control flow and charges you a migration. This only adds a **gate** (budgets, approval, repeats) and a **ledger** (stats, traces).

Test: **remove the layer — does your loop still run?** If yes, it is an upgrade, not a rewrite.

### Layout

| Path | What |
|---|---|
| `loopkit/upgrade.py` | **the main line**: guard, toolbox, stats, traces |
| `loopkit/` (kernel, patterns, advisor) | reference implementation used to run the benchmarks |
| `dsh-plugin/` | DSH integration of the advisor |
| `01`–`05` notes | background research |
| `docs/POSITIONING.md` | positioning, non-goals, acceptance criteria |

### Out of scope

Graph engine · MCP/memory/vector DB · hosting/UI · multi-agent protocols · streaming policy.

### Quickstart

```powershell
git clone https://github.com/Yunzenn/harness-lab.git
cd harness-lab
$env:PYTHONPATH="loopkit"
python -m unittest discover -s loopkit/tests -t loopkit
python loopkit/bench/uplift_bench.py
```

### Limits

Simulated tool latency; threads rather than asyncio; not on PyPI yet (`loopkit` is taken); no real-model numbers yet.

MIT License.

