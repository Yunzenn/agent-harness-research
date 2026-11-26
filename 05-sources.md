# 05 · 资料库：读什么、为了什么

> 原则：**一手优先**。本机能直接读的实现（DSH 插件 README）排第一，官方文档排第二，论文排第三，二手博客排最后。
> 标注 ⚠️ 的是「数字会被引用到正文」的条目，改动时注意别过期。

---

## 1. 本机一手：DSH 插件实现

路径：`D:\node\node_global\node_modules\@deepseek-ai\dsh\node_modules\@deepseek-ai\dsh-*\README.md`
（多数包同时提供 `README.zh.md`，中文版更省事）

| 包 | 读它为了什么 | 关键事实/数字 |
|---|---|---|
| `dsh-agent-loop` | 循环的权威实现：turn/step/attempt、提示词协调、失败收尾 | 无内置轮次预算，须用 `agent/turn-stopping`；重试复用同一组装 |
| `dsh-session` | 「model-visible means logged」的具体含义 | 每个 attempt 恰好一条结算；替换只能 `{op:'replace',startSeq,endSeq}` |
| `dsh-tools` | 工具流水线与 schema 投影 | pre-execute → guard → execute → post-execute → finalize → result；`restrict()` 掩码取交集 |
| `dsh-compaction` | 压缩是一个事务 | `start → summary → 一次 replace → end`；切点须 `toolPairingBalanced*` |
| `dsh-system-prompt` | 提示词组装顺序与 KV 决策 | identity `-1000`、persona `0`、plan `500`、suffix `10000+`；`in-history` 可保前缀 |
| `dsh-plan-mode` | 「计划」是软约束的样本 | 只写一条 log-only 事件 + 一条普通用户消息；不限制工具 |
| `dsh-repeat-tool-reminder` | 循环卫生怎么做 | 阈值 `[3,5,8]`；只提醒不改结果；compaction 不重置计数 |
| `dsh-output-retention` / `dsh-spill` | 大输出治理 | 按 UTF-8 字节；`dsh-spill-policy` 的 `maxInlineTokens` 例 12500；溢出失败不改成功状态 |
| `dsh-token-meter` | 上下文压力怎么量 | 4 字符/token 启发式；明确低估 CJK 与 JSON schema |
| `dsh-mcp-client` | MCP 的工程化接入 | `mcp__server__tool` 命名；`maxInstructionBytes=32768`；重连 500ms→30s、10 次上限 |
| `dsh-skill` | 渐进披露的实现 | list 不读正文；正文不缓存；stale selection 被拒 |
| `dsh-subagent` | 子代理的并发与契约 | `maxActiveSubagents=8`，满了不排队；fork 继承 composition |
| `dsh-agent-preset` | 声明式 agent 组合 | preset 是「persona + tools + compaction + 隔离 scope」 |
| `dsh-user-approval` / `dsh-sandbox-policy` | 信任模型 | `never` 模式在 waterfall 前确定性拒绝；三档沙箱；无 allow-always 记忆 |
| `dsh-workflow` / `dsh-tool-workflow` | 工作流即代码 | 模型写 JS 扇出；前台等待；无检查点、无跨 agent token 预算 |

**用法建议**：不要通读。按 [01](01-loop-kernel.md)–[03](03-tools-skills-mcp.md) 的引用点去读对应包，一次读一个机制。

---

## 2. Codex 与 Claude Code（第三方 harness 对照）

> 取证边界：OpenAI 文档站与 raw.githubusercontent 在本机被 403，Codex 侧事实来自 GitHub API 目录树 + Software Heritage 归档 + 二手分析（涉及「审批模式命名」处均标注未核实）；Claude Code 侧来自 `code.claude.com/docs/en/*.md` 直取的官方 Markdown。

### 2.1 Codex CLI：沙箱优先的 Rust 单体

- **结构**：单一 Rust workspace `codex-rs`。分层为 `core`（会话/回合）、`protocol`、`tools`、`exec`、`sandboxing`、`execpolicy`、`apply-patch`、`mcp-server` / `rmcp-client`、`app-server`（+ protocol/transport/daemon/client）、`tui`、`cli`、`skills`、`rollout`/`thread-store`、`otel`。**更正一个流行说法**：TUI 是 Rust crate，不是 TypeScript（"Rust core + TS TUI" 是 2025 年的旧描述）。
- **沙箱**：macOS 用 Seatbelt（SBPL 策略文件：base / network / preferences / read-only platform defaults）；Linux 用 `bwrap`（bubblewrap）+ PID namespace；Windows 有独立模块。**当前树里没有 landlock**——"Seatbelt/Landlock" 的说法已过时。
- **停止**：循环到「模型不再发起工具调用」为止，停止权在模型。唯一的「别停」机制是 `/goal`：回合末尾注入 continuation 模板 + 预算上限，在 token 预算内反复续跑——**这是 Ralph loop 的官方实现，且停留在提示层，不是状态机**。
- **其它**：compaction 存在但策略未公开；子代理默认 explorer/worker/default，可用 `~/.codex/agents/*.toml` 自定义并各自指定模型；系统提示按模型分文件（`models.json`）。

### 2.2 Claude Code：规则引擎优先的会话内编排

- **回合**：一次模型输出 + 其工具执行；直到「无工具调用的回复」才结束；`max_turns` / `max_budget_usd` 封顶。
- **停止可被否决**：`Stop` / `SubagentStop` 钩子退出码 2 会**阻止停止**并把会话推回继续工作。名义上模型决定停止，控制层可否决——这是与 Codex 最本质的差别之一。
- **权限是代码**：deny → ask → allow，首个匹配胜出；**裸工具名的 deny 会把工具从模型上下文里整个移除**（不是执行时拒绝）。六种模式（default / acceptEdits / plan / auto / dontAsk / bypassPermissions），auto 用后台分类器代替人审。CLAUDE.md 里写「允许」无效——规则由 harness 执行。沙箱是独立的第二层，与权限叠加。
- **钩子**：三十余个事件（PreToolUse / PostToolUse / PermissionRequest / Stop / PreCompact / PostCompact / UserPromptSubmit / InstructionsLoaded …），五类 handler（command / http / mcp_tool / prompt / agent）；PreToolUse 可 deny，`additionalContext` 以 system-reminder 注入。**坑：钩子崩溃默认放行**，要显式 `onFailure: block` 才阻断。
- **扩展面**：CLAUDE.md（每请求重注入，auto memory 只读 MEMORY.md 前 200 行 / 25KB）；Skills（描述常驻、正文按需，`allowed-tools` 临时授权、`disable-model-invocation` 可彻底隐藏、`/skill-doctor` 量化上下文成本）；子代理（md + frontmatter，可各带 tools/model/permissionMode/hooks，默认嵌套 3 层、并发 20，**报告会被「指令形状」扫描以防注入**）；MCP（tool search 默认延迟 schema，只留工具名与服务器说明）；插件市场打包 skills+agents+hooks+MCP。

### 2.3 机制差异

| 维度 | Codex | Claude Code |
|---|---|---|
| 停止 | 模型决定；「不停止」靠末尾注入提示 + token 预算（**提示层**） | 模型决定；「不停止」靠钩子否决 + max_turns/预算（**控制层，可编程**） |
| 上下文 | 有 compaction，无可编程干预点 | compact boundary + Pre/PostCompact 钩子 + 可在 CLAUDE.md 声明「压缩时保留什么」 |
| 权限 | **先削能力再问**：OS 沙箱是一等公民，审批是沙箱档位的补充 | **先问再削能力**：规则引擎 + 分类器是一等公民，沙箱是可选第二层 |
| 扩展 | 收敛：TOML + AGENTS.md + skills + subagent TOML + MCP | 五个正交层（记忆/技能/子代理/钩子/插件），表达力强但组合爆炸 |
| 多代理 | 服务化：app-server 协议 + agents 目录 + 消息板 | 会话内嵌套：深度/并发硬上限 + worktree 隔离 + 跨会话消息 |

### 2.4 五条可迁移的规则

1. **上下文是预算，不是容器**：目标是「最小高信号 token 集」，不是塞满。
2. **工具蔓延是头号失败模式**（Anthropic 原话 "bloated tool sets"）；Claude Code 用 `/doctor`、`/skill-doctor` 量化每个技能/MCP 的上下文成本——**成本要能被测量才管得住**。
3. **权限疲劳要靠机制解决**：分类器、沙箱内自动放行、dontAsk——而不是教育用户多点「允许」。
4. **压缩要能注入意图**：在记忆文件里声明「压缩时保留目标/文件/决策」，并保留最近 N 个文件。
5. **子代理输出是外部输入**：Claude Code 对子代理报告做注入扫描并加「无授权」标头——**结构性防注入，而不是靠提示词**。

一条元教训：Codex 的文档迁出仓库后只剩 100–700 字节的 stub，外界仍在传「Landlock」这种过期事实。**架构决策要写进仓库内的 ADR，否则半年后连自己人都会记错。**

---

## 3. Anthropic 工程博客：模式名的出处

| 文章 | 用来读 | 已用到的结论 |
|---|---|---|
| [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents) | 五工作流派与 agents 的判据 | workflows = 预定义代码路径；agents = 模型自定流程；「先用最简单的方案」 |
| [Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) | 长任务的三个杠杆 | compaction / structured note-taking / sub-agent（工人烧数万 token 只回 1–2k） |
| [Writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents) | 工具设计原则 | tools are a contract；整合、命名空间、token 效率（默认截断 25,000） |
| [Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) | 长任务 harness 的骨架 | initializer + coding agent；进度文件 + feature list + git；四个失败模式 |
| [Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills) | 渐进披露 | 三层（name+desc → SKILL.md → 附件），因此可打包「实际无界」的上下文 |
| [Claude Code best practices](https://code.claude.com/docs/en/best-practices) | 现行实践（2026 版已改写） | 旧建议被列入「可以停止做的事」：靠上下文窗口、用 subagent 保上下文、全大写强调、loop until done；**现行主张是给 agent 一个 pass/fail 的检查** |

⚠️ 一个容易被引错的点：2025 年的「context window is your main constraint / use subagents to protect context」在 2026 版里已被降级。引用前先看日期。

---

## 4. 研究：循环为什么坏

| 论文/报告 | 一句话 | 用到的数字 |
|---|---|---|
| [ReAct](https://arxiv.org/abs/2210.03629) | 推理与行动交错，外部观察抑制幻觉 | ALFWorld +34%、WebShop +10%（绝对成功率） |
| [Reflexion](https://arxiv.org/abs/2303.11366) | 语言反馈写入情景记忆，不更新权重 | HumanEval 91% pass@1（GPT-4 80%） |
| [Self-Refine](https://arxiv.org/abs/2303.17651) | 同一模型自我批判与修订 | 7 任务平均 +~20% |
| [Tree of Thoughts](https://arxiv.org/abs/2305.10601) | 对思维做搜索而非线性推进 | Game of 24：4% → 74% |
| [MAST](https://arxiv.org/abs/2503.13657) | 多智能体失败的 3 族 14 模式 | 1,600+ 轨迹、7 框架、κ=0.88；失败率 41%–86.7%；单点干预 +9.4% |
| [Context Rot（Chroma）](https://www.trychroma.com/research/context-rot) | 输入变长即退化，且非均匀 | 18 模型；单个 distractor 即掉分；113k vs 300 token |
| [NoLiMa](https://arxiv.org/abs/2502.05167) | 无字面匹配时长上下文更差 | 32K 处 13 个模型中 11 个跌破基线 50% |
| [RULER](https://arxiv.org/abs/2404.06654) | 宣称的长窗口大多不成立 | 32K 仍「satisfactory」的仅约一半 |
| [LLMs Get Lost in Multi-Turn](https://arxiv.org/abs/2505.06120) | 多轮平均掉 39%，走错难恢复 | 分解为「能力小损 + 不可靠性大增」 |

**怎么用**：这些数字不支持「更长的上下文更好」，也不支持「更多轮更好」。它们支持 [01](01-loop-kernel.md) 的成本模型与 [02](02-scenario-to-workflow.md) 的「验证密度」主轴。

---

## 5. 编排与持久执行

| 系统 | 一句话 | 关键约束 |
|---|---|---|
| [Temporal](https://docs.temporal.io/workflows) | 事件历史重放，副作用放 Activity | workflow 必须确定性；改代码要版本化；Schedule 默认 Overlap=Skip |
| [LangGraph](https://docs.langchain.com/oss/python/langgraph/persistence) | 图 + checkpointer，thread_id 当游标 | checkpoint 在 super-step 边界 → at-least-once，节点整体重跑 |
| [Inngest](https://www.inngest.com/docs/durable-execution/durable-workflows) | `step.run` 结果做检查点 | step 外的代码会被重放 |
| [Restate](https://docs.restate.dev/foundations/key-concepts) | journal + exactly-once invocation | 幂等键去重；Virtual Object 单写者 |
| [DBOS](https://docs.dbos.dev/architecture) | Postgres 即编排器 | workflow 确定性 + step 幂等 |

**给 agent 设计的启示**：这些系统解决的是 [02](02-scenario-to-workflow.md) L7 那一格——**崩溃重放、幂等、等待、审计**。如果你的 agent 只是「一次会话内多步」，不需要它们；如果跨天、等人、定时，绕不过去。

---

## 6. MCP：规范与安全

| 资料 | 用到的点 |
|---|---|
| [MCP 规范 2026-07-28](https://modelcontextprotocol.info/specification/2026-07-28/changelog/) | 去会话化；每请求 `_meta` 带版本；`server/discover`；MRTR 取代服务端反向请求 |
| [Tools 概念](https://modelcontextprotocol.info/docs/concepts/tools/) | name 规则、`readOnlyHint` 等**仅提示、必须视为不可信**、`ttlMs`/`cacheScope` |
| [Security best practices](https://modelcontextprotocol.info/docs/2026-07-28/tutorials/security/security_best_practices/) | confused deputy、token passthrough、SSRF、state handle hijacking |
| [Advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use) | 工具搜索：~500 常驻 + 按需 3–5 个 ≈ 8.7K（-85%）；准确率 49%→74% / 79.5%→88.1% |
| [Code execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp) | 文件树按需读定义：150,000 → 2,000 token；程序化调用 -37%、省 19+ 轮 |
| [Claude Code sandboxing](https://www.anthropic.com/engineering/claude-code-sandboxing) | 沙箱内自由 vs 逐次批准：权限提示减少 84% |

---

## 7. 实践者模式（二手，但有细节）

| 来源 | 内容 | 提醒 |
|---|---|---|
| [Ralph loop](https://ghuntley.com/ralph/) | `while :; do cat PROMPT.md | agent; done`，每轮全新上下文 | 作者自述仅适合 greenfield，期望「90% 完成」 |
| [Kiro specs](https://kiro.dev/docs/specs/) | requirements → design → tasks 三件套 | 规格漂移会变成双源 |
| SWE-bench Lite 采样数据 | 单样本 15.9% → 250 样本 56% | majority voting / reward model 在数百样本后 plateau |

---

## 8. 关键数字速查

| 数字 | 含义 | 出处 |
|---|---|---|
| 55K tokens | 5 个 MCP server、58 个工具的定义开销 | Anthropic |
| 134K | 见过的最大工具定义开销 | Anthropic |
| -85% | 工具搜索带来的定义 token 下降 | Anthropic |
| 191,300 / 122,800 | 工具搜索前后的可用上下文 | Anthropic |
| 150,000 → 2,000 | Code Mode 读定义的开销 | Anthropic |
| 25,000 | Claude Code 默认工具输出截断 | Anthropic |
| 8,192 | DSH 工具结果裁剪阈值（技能正文同受限） | `dsh-compaction-tool-result-pruner` |
| 32,768 | MCP instructions 字节上限 | `dsh-mcp-client` |
| 8 | 默认最大活跃子代理数 | `dsh-subagent` |
| 3/5/8 | 重复调用提醒阈值 | `dsh-repeat-tool-reminder` |
| 84% | 沙箱化后权限提示的下降 | Anthropic |
| 41%–86.7% | 多智能体系统失败率区间 | MAST |
| 39% | 多轮对话平均性能下降 | LLMs Get Lost in Multi-Turn |

---

## 9. 三条阅读路径

**A. 我要设计一个 agent（半天）**
[02](02-scenario-to-workflow.md) → [01](01-loop-kernel.md) §1–4 → `dsh-agent-loop` + `dsh-tools` README → 写自己的第一版 kernel。

**B. 我要接外部工具（两小时）**
[03](03-tools-skills-mcp.md) → `dsh-mcp-client` README → MCP 规范 tools 页 + 安全最佳实践 → 算一遍 token 账再决定。

**C. 我要让它上生产（一天）**
[04](04-failure-and-eval.md) → MAST 原文（当检查表用）→ 选一个持久执行引擎的文档 → 设计四本预算与放权分级。
