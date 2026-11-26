# loop_lab：一个可以争论的最小 harness

这不是一个 agent 框架，是一个**用来验证论断的实验台**。纯标准库、离线、确定性；「模型」是脚本化策略，所以每次运行的轨迹完全一样，可以逐行对照。

```powershell
python run_all.py            # 轨迹 + 成本表 + 9 项自检
python run_all.py --quiet    # 只看表
```

## 为什么这样切

```
kernel.py     ← 不变的部分：日志、工具流水线、预算、循环
scenarios.py  ← 变化的部分：五种控制流 + 五个业务场景
```

如果「工作流」需要改 kernel，说明 kernel 设计错了。这个仓库本身就是对 [01](../01-loop-kernel.md) 那条主张的检验：**同一个循环，能长出完全不同的工作流。**

## kernel 的九个决定

| 决定 | 实现 | 对应笔记 |
|---|---|---|
| 真相是日志，不是消息数组 | `SessionLog`（append-only）+ `messages()` 投影 | I1 |
| 一次 step = 一次组装 + 一次请求 + 一批工具 | `Harness.step()` | I3 |
| 每个 call 恰好一个 result，失败也配对 | `Registry.execute()` 永不抛，返回结构化错误 | I2 / I8 |
| 停止条件在模型之外 | `run(stop)` 接受外部谓词 | I4 |
| 预算四项、硬性、先于一切 | `Budget`（steps / calls / chars / wall） | I5 |
| 取消保留已交付文本 | `StepDecision`/`note` 事件（简化版） | I6 |
| 上下文只追加或显式替换 | 快照/恢复走 `SessionLog.snapshot/restore` | I7 |
| 成本可观测 | 每步 `est_chars`，记 `peak` 与 `total` | 01 §2 |
| 工具 schema 即提示词 | `Tool.schema()` 只投影模型需要的字段 | 03 §2 |

## 五个场景证明了什么

| 场景 | 拓扑 | 断言 |
|---|---|---|
| S1 修 bug | ReAct + 测试判据 | 判据硬，循环可以很笨（一次故意写错的修复被测试抓回来） |
| S2a 写要点（自评） | ReAct | **假成功**：零引用，status=ok |
| S2b 写要点（rubric） | 计划-执行-校验 | 多花一倍 token，换来「每条要点有引用」 |
| S3 读 6 篇 | 单上下文 vs 扇出 | 峰值 1599 < 2498，总账 9585 > 8144 |
| S4 工单分诊 | 路由 + 窄工具 | 每类工具面不同；可疑工单 `unrouted` 而不是硬塞 |
| S5 退款 | 守卫 + 闸门 + 恢复 | 审批前什么都没发生；恢复后只执行一次 |

`run_all.py` 末尾的 9 项自检就是这些断言的回归测试。

## 局限（别把它当框架用）

- **模型是假的。** `ScriptedPolicy` 是规则，不是 LLM。它不能测量模型能力，只能暴露 harness 结构。
- **没有真并发。** `parallel_safe` 只是标注；`execute_batch` 顺序执行。扇出的成本按串行累加，所以 S3 的总账对比是「同等执行方式下」的对比。
- **没有 KV cache / 压缩 / 溢出。** 这三件事在 [01 §5](../01-loop-kernel.md) 里讲，代码里没实现——实现了反而会掩盖成本结构的清晰度。
- **审批是模拟的。** `gate` 事件 + 恢复足够演示语义，但没有真实的人机通道与超时。
- **token 是字符估算**（4 字符/token），对中文与 JSON schema 都低估。

## 想继续做的话

1. 给 `Harness` 加 `compact()`（带配对平衡检查）与 `spill()`，看成本曲线怎么变。
2. 把 `ScriptedPolicy` 换成真实模型，保留同一套 stop 谓词，对比「同 harness 不同模型」。
3. 加一个 `eval.py`：跑 N 次随机故障注入，统计「配对是否完整」「假成功率」。
4. 把 S5 的闸门换成真正的中断/恢复（进程重启），验证日志重放。
