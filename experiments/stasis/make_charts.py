"""Charts for the stasis experiment, computed from a traces file.

    python experiments/stasis/make_charts.py model_traces_7b.json
"""

from __future__ import annotations

import json
import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from analyze import classify, detect_novelty, detect_progress  # noqa: E402
from contract_eval import replay  # noqa: E402

TEAL = "#0E7C66"
TEAL_LIGHT = "#7FBFB0"
GREY = "#C9CED6"
INK = "#1F2933"
MUTED = "#6B7785"

plt.rcParams.update(
    {
        "font.family": "DejaVu Sans",
        "font.size": 11,
        "axes.edgecolor": "#D5DAE1",
        "text.color": INK,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "figure.facecolor": "white",
        "axes.facecolor": "white",
    }
)

OUT = os.path.join(HERE, "figures")
os.makedirs(OUT, exist_ok=True)


def clean(ax, axis="y"):
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.grid(axis=axis, color="#EEF1F4", linewidth=1)
    ax.set_axisbelow(True)


def metrics(traces: list[dict]) -> dict:
    failed = [t for t in traces if not t["done"]]
    healthy = [t for t in traces if t["done"]]
    caught = {
        "novelty": sum(1 for t in failed if detect_novelty(t)),
        "progress": sum(1 for t in failed if detect_progress(t)),
        "contract": sum(1 for t in failed if replay(t)["caught_kind"]),
    }
    recall = {k: (v / len(failed) if failed else 0.0) for k, v in caught.items()}
    false_flags = {
        "novelty": sum(1 for t in healthy if detect_novelty(t)),
        "progress": sum(1 for t in healthy if detect_progress(t)),
        "contract": sum(1 for t in healthy if replay(t)["caught_kind"]),
    }
    modes: dict[str, int] = {}
    for t in traces:
        m = classify(t)
        modes[m] = modes.get(m, 0) + 1
    rows = [replay(t) for t in failed]
    saved = sum(r["calls_saved"] for r in rows)
    used = sum(r["calls"] for r in rows)
    return {
        "n": len(traces),
        "failed": len(failed),
        "healthy": len(healthy),
        "recall": recall,
        "false_flags": false_flags,
        "modes": modes,
        "saved": saved,
        "stalled_calls": used,
    }


def chart_detectors(m: dict) -> None:
    names = ["world signature\n(novelty)", "progress stalled", "contract\n(done + progress)"]
    vals = [m["recall"]["novelty"] * 100, m["recall"]["progress"] * 100, m["recall"]["contract"] * 100]
    fig, ax = plt.subplots(figsize=(8.6, 4.2))
    bars = ax.bar(names, vals, color=[GREY, TEAL_LIGHT, TEAL], width=0.55)
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 2, f"{v:.0f}%", ha="center", fontsize=13, fontweight="bold",
                color=TEAL if v >= 99 else INK)
    ax.set_ylim(0, 115)
    ax.set_ylabel("failures caught (%)")
    ax.set_title("Same failures, three detectors", loc="left", fontsize=13, fontweight="bold", pad=14)
    ax.text(0.0, -0.2, f"{m['n']} real traces · {m['failed']} failed · {m['healthy']} healthy · false flags: "
                        f"novelty {m['false_flags']['novelty']}, progress {m['false_flags']['progress']}, "
                        f"contract {m['false_flags']['contract']}",
            transform=ax.transAxes, fontsize=9, color=MUTED)
    clean(ax)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "detectors.png"), dpi=200, bbox_inches="tight")
    plt.close(fig)


def chart_modes(traces: list[dict]) -> None:
    kinds = sorted({t["kind"] for t in traces})
    color = {"completed": GREY, "drifting": TEAL, "premature_stop": TEAL_LIGHT, "frozen": "#4D8F80"}
    labels, colors, counts = [], [], []
    for k in kinds:
        modes = [classify(t) for t in traces if t["kind"] == k]
        dominant = max(set(modes), key=modes.count)
        labels.append(k)
        colors.append(color.get(dominant, GREY))
        counts.append(len(modes))
        m = dominant
    fig, ax = plt.subplots(figsize=(8.8, 3.4))
    bars = ax.barh(labels, counts, color=colors, height=0.5)
    for b, k in zip(bars, kinds):
        modes = [classify(t) for t in traces if t["kind"] == k]
        dominant = max(set(modes), key=modes.count)
        ax.text(b.get_width() + 0.1, b.get_y() + b.get_height() / 2, f"{len(modes)} × {dominant}",
                va="center", fontsize=11, fontweight="bold")
    ax.set_xlim(0, max(counts) + 4)
    ax.set_xlabel("traces")
    ax.set_title("Each workload fails in its own way", loc="left", fontsize=13, fontweight="bold", pad=14)
    clean(ax, axis="x")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "failure-modes.png"), dpi=200)
    plt.close(fig)


def chart_savings(traces: list[dict]) -> None:
    stalled = [t for t in traces if not t["done"]]
    rows = [replay(t) for t in stalled]
    labels = [f"{r['kind']}\n#{i + 1}" for i, r in enumerate(rows)]
    used = [r["calls"] for r in rows]
    saved = [r["calls_saved"] for r in rows]
    x = list(range(len(rows)))
    fig, ax = plt.subplots(figsize=(max(8.5, len(rows) * 0.55), 4))
    ax.bar(x, used, color=GREY, width=0.6, label="calls used")
    ax.bar(x, saved, color=TEAL, width=0.6, label="calls saved by the contract")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylabel("tool calls")
    ax.set_title("Early stop: calls saved per stalled trace", loc="left", fontsize=13, fontweight="bold", pad=14)
    ax.legend(frameon=False, loc="upper right")
    clean(ax)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "savings.png"), dpi=200)
    plt.close(fig)


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "model_traces.json"
    traces = json.load(open(os.path.join(HERE, name), encoding="utf-8"))
    m = metrics(traces)
    chart_detectors(m)
    chart_modes(traces)
    chart_savings(traces)
    print(json.dumps(m, ensure_ascii=False, indent=1))
    print("wrote figures to", OUT)


if __name__ == "__main__":
    main()

