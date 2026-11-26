# 定位：给已有 loop 加装的一层

> 一句话：**不造框架。你的 loop 不动，加三个调用。**

---

## 为什么不是框架

框架要接管控制流（图、状态机、调度器），你要做的是把整个 agent 迁进去。这不是"提升"，是"重写"。

`loopkit` 的形状相反：它是一个**加装层**。你已经有一个 while 循环、一张 LangGraph 图、或者一个 DSH 会话——在它外面包三样东西即可，控制流一行不改。

判断标准很简单：**如果去掉 loopkit，你的 loop 还能跑吗？** 能，那就是加装层。

---

## 三个能力（每一个都可测）

| 能力 | 机制 | 可测指标 |
|---|---|---|
| **花得少** | 工具缓存（TTL）、批内去重、有界并发 | 真实执行次数 ↓、墙钟 ↓ |
| **停得对** | 四本预算、重复调用拦截、破坏性操作审批 | 失控 loop 100% 在预算内停 |
| **看得见** | `LoopStats` + `trace.jsonl` | 每步/每调用可回放 |

---

## 契约（10 行）

```python
from loopkit.upgrade import GuardConfig, LoopGuard, ToolBox, ToolDenied

guard = LoopGuard(GuardConfig(max_steps=12, max_tool_calls=20, cache_ttl_s=60))
box = ToolBox({"read": read_file, "run_tests": run_tests}, guard=guard)

while True:
    if guard.begin_step(tokens_in=usage):      # 预算/墙钟到了就停
        break
    try:
        out = box.call("read", {"path": "a.py"})   # 缓存 + 重复拦截
    except ToolDenied as exc:
        out = f"skipped: {exc.reason}"             # 拒绝是数据，不是崩溃

print(guard.finish().as_row())
```

没有 `Agent` 类，没有 `Graph`，没有 `Runner`。你的循环仍然是你的循环。

---

## 实测：同一个 loop，只换调用层

`bench/uplift_bench.py`，工具耗时 40ms，同一段循环代码跑两遍：

| 场景 | 裸循环执行次数 | 加装后 | 降幅 | 墙钟降幅 | 停止原因 |
|---|---:|---:|---:|---:|---|
| 12 个工单（重复读两遍） | 24 | 12 | **−50%** | −50% | loop_exit |
| 失控重试（永不收敛） | 50 | 12 | **−76%** | −76% | `max_tool_calls` |
| 同一调用重复 10 次 | 10 | 3 | **−70%** | −70% | loop_exit |

第三行值得注意：**不是把一个调用变快，而是拒绝执行第 4 次**——循环自己会做别的事，或者由模型看到拒绝原因后换策略。

---

## 非目标（写下来是为了不做）

- ❌ 通用图引擎（LangGraph 的地盘）
- ❌ MCP / 记忆 / 向量库（应作为可选依赖，不自己造）
- ❌ 托管平台 / UI
- ❌ 多 agent 协议（handoff、A2A）
- ❌ 流式与非流式的取舍（宿主决定，我们只包工具与预算）

---

## 适配器

| 宿主 | 状态 | 说明 |
|---|---|---|
| 纯 Python（while/for） | ✅ 已可用 | `upgrade.py` 就是为它写的 |
| DSH 插件 | ◐ 已有 advisor | 下一步把 guard 暴露成会话级预算 |
| LangGraph | ○ 计划 | 用 callback 包 node，不动图 |
| OpenAI Agents SDK | ○ 计划 | 包 function_tool |

适配器的写法统一：**把宿主的"调用工具"和"进入下一步"两个动作，转发给 `ToolBox` 和 `LoopGuard`。**

---

## 验收标准

一个 PR 合入前，必须能用 `uplift_bench` 同类脚本回答：

1. 同一个 loop，接入后**真实执行次数**降了多少？
2. 失控 loop 是否在预算内停止，停止原因是否可读？
3. trace 能否回放出"第几步、调了什么、为什么被拒"？

三条都答不上来，就还不是"提升"，只是"多了一层"。
