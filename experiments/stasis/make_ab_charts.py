"""Charts for the with/without benchmark (ab_results.json)."""

from __future__ import annotations

import json
import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "figures")
os.makedirs(OUT, exist_ok=True)

TEAL = "#0E7C66"
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


def clean(ax, axis="y"):
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.grid(axis=axis, color="#EEF1F4", linewidth=1)
    ax.set_axisbelow(True)


def bars(ax, labels, baseline, contract, title, ylabel, fmt="{:.0f}", ymax=None):
    x = range(len(labels))
    w = 0.34
    b1 = ax.bar([i - w / 2 for i in x], baseline, width=w, color=GREY, label="without loopkit")
    b2 = ax.bar([i + w / 2 for i in x], contract, width=w, color=TEAL, label="with contract")
    top = max(baseline + contract) if baseline or contract else 1
    for i, (a, b) in enumerate(zip(baseline, contract)):
        ax.text(i - w / 2, a + top * 0.02, fmt.format(a), ha="center", fontsize=10, color=MUTED)
        ax.text(i + w / 2, b + top * 0.02, fmt.format(b), ha="center", fontsize=10, fontweight="bold", color=TEAL)
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels)
    ax.set_ylabel(ylabel)
    ax.set_ylim(0, ymax if ymax else top * 1.25)
    ax.set_title(title, loc="left", fontsize=13, fontweight="bold", pad=14)
    ax.legend(frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.16), ncols=2, fontsize=10)
    clean(ax)


def main() -> None:
    rows = json.load(open(os.path.join(HERE, "ab_results.json"), encoding="utf-8"))
    kinds = ["batch", "tickets", "rag"]

    # 1. false "done" deliveries
    fd_base = [sum(r["false_done"] for r in rows if r["kind"] == k and r["mode"] == "baseline") for k in kinds]
    fd_cont = [sum(r["false_done"] for r in rows if r["kind"] == k and r["mode"] == "contract") for k in kinds]
    fig, ax = plt.subplots(figsize=(8.6, 4))
    bars(ax, kinds, fd_base, fd_cont, "False “done” deliveries", "runs", ymax=max(fd_base + fd_cont) * 1.4 + 1)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "ab-false-done.png"), dpi=200)
    plt.close(fig)

    # 2. final task progress
    def avg(kind, mode):
        sub = [min(1.0, r["progress"]) for r in rows if r["kind"] == kind and r["mode"] == mode]
        return sum(sub) / len(sub) if sub else 0

    pg_base = [avg(k, "baseline") for k in kinds]
    pg_cont = [avg(k, "contract") for k in kinds]
    fig, ax = plt.subplots(figsize=(8.6, 4))
    bars(ax, kinds, pg_base, pg_cont, "Task progress when the run ends", "progress (0–1)", fmt="{:.2f}", ymax=1.12)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "ab-progress.png"), dpi=200)
    plt.close(fig)

    # 3. tool calls per run
    def calls(kind, mode):
        sub = [r["calls"] for r in rows if r["kind"] == kind and r["mode"] == mode]
        return sum(sub) / len(sub) if sub else 0

    cl_base = [calls(k, "baseline") for k in kinds]
    cl_cont = [calls(k, "contract") for k in kinds]
    fig, ax = plt.subplots(figsize=(8.6, 4))
    bars(ax, kinds, cl_base, cl_cont, "Tool calls per run", "calls")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "ab-calls.png"), dpi=200)
    plt.close(fig)

    print("false_done:", dict(zip(kinds, zip(fd_base, fd_cont))))
    print("progress:", {k: (round(a, 2), round(b, 2)) for k, a, b in zip(kinds, pg_base, pg_cont)})
    print("calls:", {k: (round(a, 1), round(b, 1)) for k, a, b in zip(kinds, cl_base, cl_cont)})


if __name__ == "__main__":
    main()



