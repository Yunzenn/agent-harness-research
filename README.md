# loopkit

**Agent 说“完成了”，不算完成。**

我们用本地模型跑了 24 次真实任务：**16 次没有真正完成**——8 次宣布完成时只做了 17%，8 次忙了十几步、进度始终是 0。

现有工具都管不了这件事：

- `max_iterations` / `recursion_limit` 只管**步数**，不知道活干完没有；
- Langfuse 之类只负责**看见**，不拦；
- LoopGain 那类收敛检测需要**可测量的 error signal**，而工具型循环（读文件、改工单）根本没有。

loopkit 只做一件事：**用 10 行把“完成”写成条件，然后在每次工具调用和结束点检查它。**

```python
from loopkit import Contract, ContractMonitor

contract = Contract(
    name="resolve tickets",
    done_when=lambda w: all(v == "resolved" for v in w.tickets.values()),
    advances=lambda w: sum(v == "resolved" for v in w.tickets.values()) / 6,
)
mon = ContractMonitor(contract, stall_steps=4)

while True:
    call = model.next(...)
    result = box.call(call.name, call.args)
    mon.observe(world)                  # 进度 4 步不涨 → drift
    mon.before_call(call.name, call.args, world)   # 违反 never → churn

    if call.name == "finish":
        if mon.before_stop(world):      # 没做完就想结束 → premature_stop
            continue                    # 不许交卷
        break
```

## 效果（24 条真实轨迹）

| 检测方式 | 抓到失败 | 误报 |
|---|---:|---:|
| 看“世界有没有变化” | 0/16 | 0 |
| 看“进度是否停滞” | 8/16 | 0 |
| **契约（done_when + advances）** | **16/16** | **0** |

![detectors](experiments/stasis/figures/detectors.png)

附带收益：在失败轨迹里提前停止，省下 **51%** 的工具调用（全部轨迹口径 30%）。

真实失败长这样，不是“卡死”：

| 负载 | 结局 | 模型在干什么 |
|---|---|---|
| batch | 8/8 完成 | 逐篇读、逐篇写摘要 |
| tickets | 8/8 漂移 | `get → set(in_progress) → append("Working on it.")`，从不结单 |
| rag | 8/8 早停 | 答到 0.17–0.83 就宣布 DONE |

## 为什么是你需要的

不是“又一个 agent 框架”，也不是“又一个看板”：

- **不改你的循环**：包住工具调用和结束点，三行；
- **不需要 error signal**：判据是任务状态，不是 loss；
- **确定性**：不调模型、不猜阈值，同样的轨迹永远同样的结论。

适合：把 agent 放进生产、被“假完成”坑过、又没有 verifier 的团队。

## 怎么用

```powershell
git clone https://github.com/Yunzenn/harness-lab.git
cd harness-lab
$env:PYTHONPATH="loopkit"

python -m unittest discover -s loopkit/tests -t loopkit   # 39 tests
python experiments/stasis/contract_eval.py experiments/stasis/model_traces.json   # 复现 16/16
```

实验代码与轨迹在 [`experiments/stasis/`](experiments/stasis/)，定位与非目标在 [`docs/POSITIONING.md`](docs/POSITIONING.md)。

## 边界

- 契约要你自己写——**这正是产品的重点**：把“完成”定义清楚，本来就是设计工作；
- 目前只在 3 类工具任务、24 条轨迹上验证；没有 verifier 的业务（客服、数据清洗）还需要更多验证；
- Python，未上 PyPI；`loopkit` 这个名字在 PyPI 被占用，发布时会改名。

MIT License.

---

# English

**“Done” is not done until the goal state says so.**

Across 24 real runs of a local model, **16 never actually finished**: 8 declared completion at 17% progress, 8 spent a dozen steps with progress pinned at 0.

`max_iterations` counts steps, not completion. Langfuse shows you the problem but cannot stop it. LoopGain-style convergence checks need a measurable error signal — tool loops (read a file, update a ticket) have none.

loopkit does one thing: **write “done” as a condition, then check it at every tool call and at the end.**

```python
from loopkit import Contract, ContractMonitor

contract = Contract(
    name="resolve tickets",
    done_when=lambda w: all(v == "resolved" for v in w.tickets.values()),
    advances=lambda w: sum(v == "resolved" for v in w.tickets.values()) / 6,
)
mon = ContractMonitor(contract, stall_steps=4)
```

## Result (24 real traces)

| Check | Failures caught | False flags |
|---|---:|---:|
| “did the world change?” | 0/16 | 0 |
| “did progress stall?” | 8/16 | 0 |
| **contract (done_when + advances)** | **16/16** | **0** |

Bonus: stopping early saves **51%** of tool calls inside failed runs (30% across all runs).

Real failures are not freezes: tickets drift (always `in_progress`, never resolved), rag stops at 17–83% and calls it done.

## Why it is different

- **No new framework**: wrap your tool call and your exit point — three lines.
- **No error signal needed**: the judge is task state, not a loss.
- **Deterministic**: no model calls, no thresholds — the same trace always yields the same verdict.

## Use it

```powershell
git clone https://github.com/Yunzenn/harness-lab.git
cd harness-lab && $env:PYTHONPATH="loopkit"
python -m unittest discover -s loopkit/tests -t loopkit
python experiments/stasis/contract_eval.py experiments/stasis/model_traces.json
```

MIT License.
