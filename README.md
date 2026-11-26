# Agent Harness Research

**循环、工作流选型，以及一个能按场景选拓扑并加速执行的工具。**
*Loops, workflow selection, and a small toolkit that picks a topology per scenario and makes it faster.*

[中文](#中文) · [English](#english)

---

<a id="中文"></a>
## 中文

### 这是什么

仓库有两部分，互为表里：

| 部分 | 内容 | 形态 |
|---|---|---|
| **研究笔记**（`01`–`05`） | Codex / Claude Code / DSH 的 harness 拆解；业务场景 → 工作流的选型地图；工具、技能、MCP、子代理四种扩展面；失败分类与放权 | 5 篇长文 |
| **`loopkit/`** | 把笔记里的结论写成可执行代码：按 brief 选拓扑 + 并行/缓存/去重/模型分层 | Python 库，纯标准库，20 项测试 |
| `loop_lab/` | 更早的原型：5 个业务场景共用同一个 kernel，逐条打印轨迹与成本 | Python 脚本 |

一句话：**笔记解释「为什么」，`loopkit` 是那些结论的最小可执行版本。**

### 30 秒上手

```powershell
git clone https://github.com/Yunzenn/agent-harness-research.git
cd agent-harness-research
$env:PYTHONPATH="loopkit"

# 1) 描述业务，拿到拓扑、预算、闸门和理由
python -m loopkit advise --goal "refund duplicate charges" --oracle rubric `
    --blast irreversible --heterogeneity many

# 2) 看同一个 kernel 跑四种控制流
python -m loopkit demo

# 3) 基线 vs 优化，实测
python -m loopkit bench

# 4) 测试
cd loopkit; python -m unittest discover -s tests -t .
```

`advise` 的输出不是一句「建议用 ReAct」，而是一份可评审的计划：

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
```

### 五条结论

1. **选工作流的主轴是「判据密度」，不是任务难度。** 测试、编译器、schema 在，循环可以又笨又自由；判据只在人心里，循环就不该闭合在模型上。
2. **`final` 不等于成功。** 模型说「做完了」只是文本；把这句话当停止条件，等于让考生自己判卷。
3. **成本的主变量是步数 × 每步固定开销。** 工具越多每一步都越贵；扇出买的是「峰值上下文」，不省总账。
4. **工具、技能、MCP、子代理是四个轴，不是竞品。** 能力 / 判断 / 供应链 / 上下文隔离。
5. **失败主要是设计问题。** 症状出现在循环层，根因通常在接口层——先改工具和契约，别先改提示词。

### 实测（loopkit）

模拟模型 80ms/次、工具 50ms/次；测的是**编排开销**，不是模型能力。

| 负载 | 基线 | 优化后 | 加速 | token 变化 |
|---|---:|---:|---:|---:|
| 12 docs（串行 vs 6 路扇出） | 2.24s | 0.38s | **6.0x** | -38% |
| 24 tickets（12 工具面 vs 3 窄工具 + 6 路并发） | 6.69s | 1.11s | **6.0x** | -75% |
| bugfix loop（并行读 + 缓存） | 0.55s | 0.38s | **1.5x** | -30% |
| 合计 | 9.47s | 1.86s | **5.1x** | |

为什么 bugfix 只有 1.5x：4 次模型调用是串行下限，并发改不了它（Amdahl）。

### 仓库结构

```
.
├── 01-loop-kernel.md            循环内核：9 个问题、8 条不变量、成本模型、停止条件、失败收尾
├── 02-scenario-to-workflow.md   五判据 → L0–L7 阶梯 → 10 模式卡 → 8 场景蓝图 → 选型流程
├── 03-tools-skills-mcp.md       四种扩展面、工具经济学、技能硬预算、MCP 信任红线、子代理契约
├── 04-failure-and-eval.md       MAST 失败分类、诊断表、三层评估、四本预算、四级放权
├── 05-sources.md                一手资料库（DSH 插件 / 官方文档 / 论文），带「读它为了什么」
├── loop_lab/                    早期原型：一个 kernel，五个业务场景，可逐行对照
│   ├── kernel.py  scenarios.py  run_all.py  README.md
└── loopkit/                     工具：advisor + kernel + patterns + 加速层
    ├── loopkit/advisor.py       场景 → 拓扑（规则引擎，输出理由）
    ├── loopkit/kernel.py        循环：日志、预算、工具批次、停止谓词
    ├── loopkit/patterns.py      react / chain / route / fanout / plan_execute_verify
    ├── loopkit/tools.py         校验、守卫、缓存、去重、有界并发
    ├── loopkit/llm.py           MockProvider / Tiered / OpenAI 兼容
    ├── bench/bench.py           基线 vs 优化的实测
    └── tests/test_loopkit.py    20 项不变量测试
```

### 七条不变量

任何 harness 都可以用这七条体检；`loopkit` 的测试就是它们的回归：

1. 日志是唯一真相（可回放）。
2. 每个工具调用恰好一个结果（失败也是结果）。
3. 停止条件在模型之外。
4. 四本预算独立：步数 / 工具调用 / token / 墙钟。
5. 拒绝是代码路径，不是提示词建议。
6. 工具 schema 即提示词（只投影模型需要的字段）。
7. 不可逆动作是屏障：串行、不缓存、需审批。

### 局限

- 模拟延迟，不是真实模型；真实长尾会让加速比下降。
- 线程不是 asyncio；没有压缩与持久缓存。
- advisor 阈值是经验值，需要用真实轨迹校准。
- 研究笔记以中文为主，英文只在 README 与关键结论层。

### 许可

代码 MIT；研究笔记 CC BY 4.0。

---

<a id="english"></a>
## English

### What this is

Two halves of one argument:

| Part | Content | Shape |
|---|---|---|
| **Research notes** (`01`–`05`) | Teardown of Codex / Claude Code / DSH harnesses; a scenario → workflow selection map; the four extension surfaces (tools, skills, MCP, subagents); failure taxonomy and staged autonomy | 5 long-form notes |
| **`loopkit/`** | The notes turned into code: pick a topology from a brief, plus parallel execution, caching, deduplication, and model tiering | Python, stdlib only, 20 tests |
| `loop_lab/` | Earlier prototype: five business scenarios on one kernel, with per-step traces and cost accounting | Python scripts |

In one line: **the notes explain *why*; `loopkit` is the smallest executable version of those conclusions.**

### Quickstart

```powershell
git clone https://github.com/Yunzenn/agent-harness-research.git
cd agent-harness-research

$env:PYTHONPATH="loopkit"
python -m loopkit advise --goal "refund duplicate charges" --oracle rubric --blast irreversible --heterogeneity many
python -m loopkit demo
python -m loopkit bench
cd loopkit; python -m unittest discover -s tests -t .
```

`advise` does not say "use ReAct". It returns a reviewable plan — topology, concurrency, tool surface, gates, four budgets, model tiers, **and the reason for each choice** (reasons can be argued with, which is the point).

### Five findings

1. **The axis for choosing a workflow is verification density, not task difficulty.** With tests and compilers around, the loop can be dumb and free. When only a human can judge, the loop must not close on the model.
2. **`final` is not success.** "I'm done" is just text; treating it as a stop condition is letting the candidate grade their own exam.
3. **Cost is steps × fixed per-step overhead.** More tools make every step more expensive; fan-out buys a lower *peak* context, not a lower total bill.
4. **Tools, skills, MCP, and subagents are four axes, not competitors.** Capability / judgement / supply chain / context isolation.
5. **Failures are mostly design failures.** Symptoms show up in the loop; root causes usually live in the interface layer — fix the tool and the contract before the prompt.

### Measured (loopkit)

Simulated 80 ms per model call, 50 ms per tool call. These numbers isolate **orchestration overhead**, not model quality.

| Workload | Baseline | Optimized | Speedup | Token change |
|---|---:|---:|---:|---:|
| 12 docs (serial vs 6-lane fan-out) | 2.24s | 0.38s | **6.0x** | -38% |
| 24 tickets (12-tool surface vs 3 narrow + 6 lanes) | 6.69s | 1.11s | **6.0x** | -75% |
| bugfix loop (parallel reads + cache) | 0.55s | 0.38s | **1.5x** | -30% |
| Total | 9.47s | 1.86s | **5.1x** | |

Why bugfix is only 1.5x: four model calls are a serial floor, and concurrency cannot change that (Amdahl).

### Repository layout

```
.
├── 01-loop-kernel.md            loop internals: 9 questions, 8 invariants, cost model, stopping, failure recovery
├── 02-scenario-to-workflow.md   five dials → L0–L7 ladder → 10 pattern cards → 8 scenario blueprints
├── 03-tools-skills-mcp.md       four extension surfaces, tool economics, skill budgets, MCP trust boundaries
├── 04-failure-and-eval.md       MAST taxonomy, diagnosis table, three-layer eval, budgets, staged autonomy
├── 05-sources.md                annotated sources (harness plugins, official docs, papers)
├── loop_lab/                    prototype: one kernel, five scenarios, trace-by-trace comparison
└── loopkit/                     the toolkit
    ├── loopkit/advisor.py       brief → topology (rule engine, with reasons)
    ├── loopkit/kernel.py        the loop: log, budgets, tool batches, stop predicate
    ├── loopkit/patterns.py      react / chain / route / fanout / plan_execute_verify
    ├── loopkit/tools.py         validation, guards, cache, dedup, bounded concurrency
    ├── loopkit/llm.py           MockProvider / Tiered / OpenAI-compatible client
    ├── bench/bench.py           measured baseline vs optimized
    └── tests/test_loopkit.py    20 invariant tests
```

### Seven invariants

1. The log is the only truth (and it can be replayed).
2. Every tool call gets exactly one result — including failures.
3. The stop condition lives outside the model.
4. Four independent budgets: steps / tool calls / tokens / wall clock.
5. Denial is a code path, not a prompt suggestion.
6. The tool schema *is* the prompt — project only what the model needs.
7. Irreversible actions are barriers: serial, uncached, approval-gated.

### Limits

- Simulated latency; real tail latency will lower the speedups.
- Threads, not asyncio; no compaction and no persistent cache.
- Advisor thresholds are heuristics that need calibration on real traces.
- Research notes are written in Chinese; English lives in this README and in the key findings.

### License

Code: MIT. Research notes: CC BY 4.0.
