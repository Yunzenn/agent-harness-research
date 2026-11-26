# loopkit

**按业务场景选拓扑，并把执行变快的最小 agent harness。**
*A tiny agent harness that picks a topology from a business brief — and makes it faster.*

[中文](#中文) · [English](#english)

---

<a id="中文"></a>
## 中文

纯标准库、无运行时依赖、约 1200 行、20 项测试。它不试图取代 LangGraph 或 Temporal：它补的是这两类工具都不管的那一层——**「这个业务该用哪种工作流」以及「怎么让它跑得更快」**。

### 为什么不用现成框架

调研（2026-11）的结论：

| 框架 | 拓扑积木 | 场景 → 拓扑决策 | 工具结果缓存 | 并行扇出 |
|---|---|---|---|---|
| LangGraph | 有（`Send` / `ToolNode`） | 否，全是手写 pattern | 否 | 有 |
| Agno | 有（`TeamMode`） | 否，开发者 `mode=` 手选 | 否 | 有 |
| AutoGen | 有（四种 team 模式） | 否，开发者手选 | 否 | 有 |
| Pydantic AI | 有（`DecisionModel` / `DynamicWorkflow`） | 部分：决策到模型级，不到拓扑级 | 缓存 prompt prefix，不缓存工具结果 | 有 |
| **loopkit** | 4 种控制流 | **是**，规则引擎且输出理由 | **是**，TTL + 单批去重 | **是**，有界 |

差别不在「能不能编排」，而在**有没有把选型本身变成代码**。

### 安装与运行

不需要安装，把目录放进 `PYTHONPATH` 即可：

```powershell
$env:PYTHONPATH="D:\path\to\harness-lab\loopkit"
python -m loopkit advise --goal "fix failing tests" --oracle code --blast write
python -m loopkit demo
python -m loopkit bench
```

或者：

```powershell
cd loopkit
python -m unittest discover -s tests -t .     # 20 项
python bench/bench.py                         # 写入 bench/RESULTS.md
```

### advisor：把选型写成代码

```python
from loopkit import Brief, advise

plan = advise(Brief(
    goal="refund duplicate charges",
    oracle="rubric",          # code | render | schema | rubric | human | none
    blast_radius="irreversible",
    lifetime="session",       # session | hours | days
    heterogeneity="many",     # single | few | many
    fanout=4,
))
print(plan.explain())
```

输出：

```
计划：L2 / route
  拓扑    L2 / route
  并发    4
  工具面  narrow-per-lane
  闸门    approval: 每个不可逆动作（代码路径，非提示）; human: 产出必须经人确认才算完成
  预算    max_steps=6, max_tool_calls=16, max_tokens_in=400000, max_seconds=60.0
  模型分层  router->small, exec->large
理由：
  - 需求分成多类且每类工具不同：先分类再进窄车道，避免一套工具打天下
  - 车道内并发 4；每条车道有独立步数上限，防止单条失控
  - 每类需求只暴露它需要的 3–6 个工具：省 token，也减少选错
  - 存在不可逆副作用：闸门必须由控制层执行，且拒绝不可被提示词推翻
  - 分类用便宜模型（20–50ms 级），执行用强模型：路由不该按强模型计费
```

计划可以直接执行：

```python
runnable = plan.build(provider=provider, registry=registry, done=lambda h: "ok" if tests_pass() else None)
result = runnable.run("fix add() in calc.py")
print(result.line())
```

缺哪个部件会显式报错并点名（`pattern 'plan_verify' needs: planner, verify`），不会猜。

### 场景 → 计划对照

| 业务 | Brief | 输出 |
|---|---|---|
| 修 bug / 迁移 | `oracle=code, blast=write` | L3 react（可验证就用最便宜的循环）；多文件 → L4 plan-verify |
| 客服工单 | `oracle=rubric, heterogeneity=many` | L2 route + 每车道窄工具 + 不匹配升级 |
| 研究报告 | `oracle=none, subtasks_known=False, fanout=12` | L5 fanout（并发 8）+ 人工闸门 + L6 警告 |
| 合规初审 | `oracle=human, blast=read` | L1 chain + 人工终审 |
| 运维值班 | `lifetime=days, blast=irreversible` | L7 + 检查点 + 幂等 + 逐次审批 |

### 实测

模拟模型 80ms/次、工具 50ms/次；每项取 2 次运行的最小值。

| 负载 | 基线 | 优化后 | 加速 | token 变化 | 说明 |
|---|---:|---:|---:|---:|---|
| 12 docs | 2.24s | 0.38s | **5.9x** | -38% | 单上下文串行 vs 6 路扇出（隔离上下文） |
| 24 tickets | 6.69s | 1.11s | **6.0x** | -75% | 12 个工具的全量 schema vs 3 个窄工具 + 6 路并发 |
| bugfix loop | 0.55s | 0.38s | **1.4x** | -30% | 逐个读 vs 并行读；重复 `run_tests` 命中缓存 |
| 合计 | 9.47s | 1.86s | **5.1x** | | |

**怎么读**：延迟是模拟的，测的是编排开销，不是模型能力；bugfix 只有 1.4x 是 Amdahl（4 次模型调用是串行下限）；token 下降是真的（`prompt_chars` 累加每次请求的完整上下文）。

### 架构

```
Brief ──► advisor（规则引擎）──► Plan ──► build() ──► Runnable
                                     │                    ├─ react        L3
                                     │                    ├─ chain        L1
                                     │                    ├─ route        L2
                                     │                    ├─ fanout       L5
                                     │                    └─ plan_verify  L4
                                     ▼
                              Budget / gates / tiers

Harness（kernel）
  assemble(system + tool schemas + messages)
      → provider.complete()
      → append to SessionLog
      → Registry.execute_batch()   [guard → cache → dedupe → parallel]
      → stop predicate（外部）
```

`kernel.py` 不知道「工作流」存在；`patterns.py` 不知道「日志」怎么存。这条边界就是可替换性的来源。

### 六种加速机制

| 机制 | 实现 | 生效条件 | 代价 |
|---|---|---|---|
| 并行工具批次 | `execute_batch` 按 `parallel_safe` 分组，线程池并发，结果保持模型顺序 | 声明 `parallel_safe=True` 且非 destructive | 线程；其余成为屏障 |
| 单批去重 | 同 `(name, 规范化 args)` 只执行一次 | `idempotent=True` | 语义误判风险 |
| TTL 缓存 | `idempotent + read_only + cache_ttl_s>0` | 只读且结果稳定 | 内存；过期策略要业务定 |
| 扇出并发 | `fanout(max_workers)`，每个工人独立日志 | 子任务彼此独立 | 总 token 可能上升 |
| 工具面收窄 | `Registry.restrict()`，schema 与执行同时收窄 | 每类角色工具不同 | 需要先分类 |
| 模型分层 | `Tiered({"router": small, "exec": large})` | 有便宜的分类/规划模型 | 两套模型各自调优 |

kernel 还记录 `prefix_ratio`（系统提示 + 工具 schema 占 prompt 的比例），它是 **KV cache 命中率的代理指标**。

### 接真实模型

```python
import os
from loopkit import Budget, Harness, OpenAICompatProvider, Registry, react

provider = OpenAICompatProvider(
    base_url="https://api.deepseek.com/v1",
    api_key=os.environ["DEEPSEEK_API_KEY"],
    model="deepseek-chat",
)
h = Harness(provider, registry, Budget(max_steps=10),
            system="You fix bugs. Run tests before claiming done.")
h.log.append("user", text="fix add() in calc.py")
res = react(h, lambda hh: "tests_pass" if world.tests_pass() else None)
```

任何兼容 `/chat/completions` 的服务都能用；`transport` 可注入，所以请求/响应整形可以离线单测（`tests/test_loopkit.py` 里就是这么测的，不需要网络）。

### 七条不变量（也是测试）

1. 日志是唯一真相（可 `checkpoint()` / `restore()`）。
2. 每个工具调用恰好一个结果（失败也变成结果，不抛异常）。
3. 停止条件在模型之外（`react(h, done)` 的 `done` 由你提供）。
4. 四本预算独立，先到先停。
5. 拒绝是代码路径（`human_gate()` 装守卫）。
6. 工具 schema 即提示词（只投影 name/description/parameters）。
7. 不可逆动作是屏障：串行、不缓存、需审批。

### 局限

- 线程而非 asyncio，不适合万级并发。
- 没有压缩（compaction），长会话会一直增长。
- 缓存是进程内的，重启即失效。
- advisor 阈值是经验值，需要真实轨迹校准。
- 基准是模拟延迟；真实模型长尾会让加速比下降。

### 路线图

- [ ] `compact()`：带配对平衡检查的压缩（工具调用不能被切开）
- [ ] `spill()`：大输出落盘 + 句柄，替代截断
- [ ] asyncio 后端，保留同步 API
- [ ] OpenTelemetry 导出（每步一条 span）
- [ ] 用真实模型重跑基准

MIT License.

---

<a id="english"></a>
## English

Standard library only, no runtime dependencies, ~1200 lines, 20 tests. It does not try to replace LangGraph or Temporal: it fills the layer neither of them covers — **which workflow this business needs, and how to make it faster**.

### Why not an existing framework

Surveyed 2026-11:

| Framework | Topology primitives | Scenario → topology decision | Tool-result cache | Parallel fan-out |
|---|---|---|---|---|
| LangGraph | Yes (`Send`, `ToolNode`) | No — hand-written patterns | No | Yes |
| Agno | Yes (`TeamMode`) | No — developer picks `mode=` | No | Yes |
| AutoGen | Yes (four team modes) | No — developer picks | No | Yes |
| Pydantic AI | Yes (`DecisionModel`, `DynamicWorkflow`) | Partial: decisions at model level, not topology level | Caches prompt prefixes, not tool results | Yes |
| **loopkit** | 4 control flows | **Yes** — rule engine with reasons | **Yes** — TTL + per-batch dedup | **Yes**, bounded |

The gap is not "can it orchestrate" — it is **whether selection itself is code**.

### Install and run

No install needed; put the directory on `PYTHONPATH`:

```powershell
$env:PYTHONPATH="D:\path\to\harness-lab\loopkit"
python -m loopkit advise --goal "fix failing tests" --oracle code --blast write
python -m loopkit demo
python -m loopkit bench
```

Or:

```powershell
cd loopkit
python -m unittest discover -s tests -t .     # 20 tests
python bench/bench.py                         # writes bench/RESULTS.md
```

### advisor: selection as code

```python
from loopkit import Brief, advise

plan = advise(Brief(
    goal="refund duplicate charges",
    oracle="rubric",          # code | render | schema | rubric | human | none
    blast_radius="irreversible",
    lifetime="session",       # session | hours | days
    heterogeneity="many",     # single | few | many
    fanout=4,
))
print(plan.explain())
```

The plan is executable:

```python
runnable = plan.build(provider=provider, registry=registry,
                      done=lambda h: "ok" if tests_pass() else None)
result = runnable.run("fix add() in calc.py")
print(result.line())
```

Missing parts are named explicitly (`pattern 'plan_verify' needs: planner, verify`) — never guessed.

### Scenario → plan

| Business | Brief | Output |
|---|---|---|
| Bug fix / migration | `oracle=code, blast=write` | L3 react (cheapest loop when verifiable); many files → L4 plan-verify |
| Support tickets | `oracle=rubric, heterogeneity=many` | L2 route + narrow tools per lane + escalation |
| Research report | `oracle=none, subtasks_known=False, fanout=12` | L5 fanout (concurrency 8) + human gate + L6 warning |
| Compliance review | `oracle=human, blast=read` | L1 chain + human sign-off |
| On-call | `lifetime=days, blast=irreversible` | L7 + checkpoints + idempotency + per-action approval |

### Measured

Simulated 80 ms per model call, 50 ms per tool call; best of 2 runs.

| Workload | Baseline | Optimized | Speedup | Token change |
|---|---:|---:|---:|---:|
| 12 docs | 2.24s | 0.38s | **5.9x** | -38% |
| 24 tickets | 6.69s | 1.11s | **6.0x** | -75% |
| bugfix loop | 0.55s | 0.38s | **1.4x** | -30% |
| Total | 9.47s | 1.86s | **5.1x** | |

**How to read it**: latency is simulated, so this measures orchestration overhead rather than model quality; 1.4x on the bugfix loop is Amdahl (four model calls are a serial floor); the token reductions are real, because `prompt_chars` counts the full context re-sent on every step.

### Architecture

```
Brief ──► advisor (rule engine) ──► Plan ──► build() ──► Runnable
                                     │                    ├─ react        L3
                                     │                    ├─ chain        L1
                                     │                    ├─ route        L2
                                     │                    ├─ fanout       L5
                                     │                    └─ plan_verify  L4
                                     ▼
                              Budget / gates / tiers

Harness (kernel)
  assemble(system + tool schemas + messages)
      → provider.complete()
      → append to SessionLog
      → Registry.execute_batch()   [guard → cache → dedupe → parallel]
      → stop predicate (external)
```

`kernel.py` does not know that "workflows" exist; `patterns.py` does not know how the log is stored. That boundary is where replaceability comes from.

### Six speed mechanisms

| Mechanism | Implementation | Precondition | Cost |
|---|---|---|---|
| Parallel tool batches | `execute_batch` groups by `parallel_safe`, runs in a thread pool, preserves model order | `parallel_safe=True` and not destructive | Threads; the rest become barriers |
| Per-batch dedup | Same `(name, canonical args)` runs once | `idempotent=True` | Semantics may surprise (e.g. "record a click") |
| TTL cache | `idempotent + read_only + cache_ttl_s>0` | Read-only and stable | Memory; TTL is a business decision |
| Fan-out | `fanout(max_workers)`, one log per worker | Independent subtasks | Total tokens may rise |
| Narrow tool surface | `Registry.restrict()` narrows both schema and execution | Different tools per role | Needs a classifier first |
| Model tiering | `Tiered({"router": small, "exec": large})` | A cheap classifier/planner exists | Two models to tune |

The kernel also records `prefix_ratio` (system prompt + tool schemas over total prompt) — a proxy for **KV-cache hit rate**.

### Real models

```python
import os
from loopkit import Budget, Harness, OpenAICompatProvider, Registry, react

provider = OpenAICompatProvider(
    base_url="https://api.deepseek.com/v1",
    api_key=os.environ["DEEPSEEK_API_KEY"],
    model="deepseek-chat",
)
h = Harness(provider, registry, Budget(max_steps=10),
            system="You fix bugs. Run tests before claiming done.")
h.log.append("user", text="fix add() in calc.py")
res = react(h, lambda hh: "tests_pass" if world.tests_pass() else None)
```

Any `/chat/completions` endpoint works. `transport` is injectable, so request/response shaping is unit-tested offline (see `tests/test_loopkit.py`).

### Seven invariants (also the tests)

1. The log is the only truth (`checkpoint()` / `restore()`).
2. Every tool call gets exactly one result — failures become results, not exceptions.
3. The stop condition lives outside the model (`done` is yours).
4. Four independent budgets; first one hit stops the run.
5. Denial is a code path (`human_gate()` installs a guard).
6. The tool schema is the prompt (name/description/parameters only).
7. Irreversible actions are barriers: serial, uncached, approval-gated.

### Limits

- Threads, not asyncio; not built for tens of thousands of concurrent calls.
- No compaction yet; long sessions keep growing.
- Cache is in-process and dies on restart.
- Advisor thresholds are heuristics that need calibration on real traces.
- The benchmark uses simulated latency; real tail latency lowers the speedups.

### Roadmap

- [ ] `compact()` with tool-pairing checks (never cut a call from its result)
- [ ] `spill()`: large outputs to disk with handles instead of truncation
- [ ] asyncio backend behind the same sync API
- [ ] OpenTelemetry export (one span per step)
- [ ] Re-run the benchmark against a real model

MIT License.
