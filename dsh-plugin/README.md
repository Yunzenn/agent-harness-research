# dsh-loopkit

**DSH 插件：在会话里问「这个任务该怎么编排」，拿到拓扑、预算、闸门和理由。**
*A DSH plugin that recommends a workflow topology — with budgets, tool surface, gates, and reasons.*

[中文](#中文) · [English](#english)

---

<a id="中文"></a>
## 中文

### 它做什么

注册一个工具 `advise_workflow`。模型（或你）给一段业务描述，它返回：

- 拓扑：L1 固定链 / L2 路由 / L3 有界 ReAct / L4 计划-执行-校验 / L5 扇出（跨天再叠 L7）
- 并发数、工具面策略、闸门清单
- 四本预算：步数 / 工具调用 / token / 墙钟
- 模型分层建议（router→small，exec→large）
- **每条决策的理由**，以及矛盾与风险提示

### 为什么是规则引擎

同一个 brief 必须得到同一个计划，否则没法在评审里讨论。模型来做这件事，你得到的是「听起来合理的建议」；规则引擎给你的是**可以被反驳的判断**。

也没有副作用：不读文件、不发网络请求、不调用模型。

### 安装

插件是纯 ESM，依赖宿主提供的 `@deepseek-ai/dsh-tools`（peer dependency）。

```powershell
# 装进某个 profile（本地路径）
dsh plugin --profile web add D:\path\to\loopkit\dsh-plugin
```

或在 profile 的 `cordis.patch.yml` 里插入：

```yaml
- insert:
    - id: dsh-loopkit
      name: dsh-loopkit
```

重启 profile 后，工具列表里会出现 `advise_workflow`。

### 用起来

```
> 我们有个退款工单系统，每天几千单，涉及计费/技术/可疑三类，退款不可逆，帮我定个工作流

（模型调用 advise_workflow(oracle="rubric", blast_radius="irreversible", heterogeneity="many", fanout=4)）

### L2 / route

| 项 | 值 |
|---|---|
| 拓扑 | L2 / route |
| 并发 | 4 |
| 工具面 | narrow-per-lane |
| 闸门 | approval: 每个不可逆动作（代码路径，非提示）; human: 产出必须经人确认才算完成 |
| 预算 | max_steps=6, max_tool_calls=16, max_tokens_in=400000, max_seconds=60 |
| 模型分层 | router→small, exec→large |

**理由**
- 需求分成多类且每类工具不同：先分类再进窄车道，避免一套工具打天下
- 车道内并发 4；每条车道有独立步数上限，防止单条失控
- 每类需求只暴露它需要的 3–6 个工具：省 token，也减少选错
- 存在不可逆副作用：闸门必须由控制层执行，且拒绝不可被提示词推翻
```

### 参数

| 参数 | 取值 | 作用 |
|---|---|---|
| `oracle` | `code` / `render` / `schema` / `rubric` / `human` / `none` | 判据密度，决定循环能不能自动收敛 |
| `blast_radius` | `read` / `write` / `irreversible` | 最坏副作用，决定闸门 |
| `lifetime` | `session` / `hours` / `days` | 寿命，`hours/days` 强制持久化（L7） |
| `heterogeneity` | `single` / `few` / `many` | 需求是否分多类，决定要不要先路由 |
| `subtasks_known` | `true` / `false` | 子任务数是否事先可知 |
| `fanout` | 整数 | 预计并行条目数 |
| `latency_budget_s` | 数字 | 墙钟预算 |
| `cost_budget_usd` | 数字 | 成本预算（折算成最大输入 token） |

非法取值不会抛错：回退到默认值并在结果顶部标注一条警告。

### 测试

```powershell
cd dsh-plugin
node --test        # 8 项
```

规则与 `loopkit/advisor.py` 一一对应；改动其中一边，请同步另一边。

### 局限

- 只是**建议**，不会自动跑工作流；真正的执行在 `loopkit`（Python）。
- 规则阈值是经验值（例如 `fanout > 8` 建议升级 L6），需要真实轨迹校准。
- 依赖宿主注入 `tools` 服务；独立的 Node 进程里不能直接用。

MIT License.

---

<a id="english"></a>
## English

### What it does

Registers one tool, `advise_workflow`. Give it a business brief and it returns:

- topology: L1 fixed chain / L2 router / L3 bounded ReAct / L4 plan-execute-verify / L5 fan-out (plus L7 when the run outlives a session)
- concurrency, tool-surface policy, gate list
- four budgets: steps / tool calls / tokens / wall clock
- model tiers (router→small, exec→large)
- **the reason for every decision**, plus contradictions and risks

### Why a rule engine

The same brief must produce the same plan, or you cannot discuss it in review. An LLM gives you a plausible-sounding recommendation; a rule engine gives you a **judgement you can argue with**.

It is also side-effect free: no file reads, no network, no model calls.

### Install

Plain ESM; `@deepseek-ai/dsh-tools` is a peer dependency provided by the host.

```powershell
dsh plugin --profile web add D:\path\to\loopkit\dsh-plugin
```

Or insert it into the profile's `cordis.patch.yml`:

```yaml
- insert:
    - id: dsh-loopkit
      name: dsh-loopkit
```

After a restart, `advise_workflow` appears in the tool list.

### Parameters

| Name | Values | Effect |
|---|---|---|
| `oracle` | `code` / `render` / `schema` / `rubric` / `human` / `none` | Verification density; decides whether the loop may close on the model |
| `blast_radius` | `read` / `write` / `irreversible` | Worst side effect; decides gates |
| `lifetime` | `session` / `hours` / `days` | `hours`/`days` force persistence (L7) |
| `heterogeneity` | `single` / `few` / `many` | Whether to route first |
| `subtasks_known` | `true` / `false` | Whether the number of subtasks is known |
| `fanout` | integer | Expected number of parallel items |
| `latency_budget_s` | number | Wall-clock budget |
| `cost_budget_usd` | number | Cost budget (converted to max input tokens) |

Invalid values never throw: they fall back to defaults and the result is prefixed with a warning.

### Tests

```powershell
cd dsh-plugin
node --test        # 8 tests
```

The rules mirror `loopkit/advisor.py` one-to-one; change both sides together.

### Limits

- It only **advises**; it does not run workflows. Execution lives in `loopkit` (Python).
- Thresholds are heuristics (e.g. `fanout > 8` suggests L6) that need calibration on real traces.
- Requires the host-injected `tools` service; it cannot run standalone in plain Node.

MIT License.

