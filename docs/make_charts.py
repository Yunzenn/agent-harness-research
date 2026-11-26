"""Generate the comparison charts used in the README (matplotlib, no CJK fonts).

    python docs/make_charts.py

Outputs docs/assets/*.png. Numbers come from bench/RESULTS.md.
"""

from __future__ import annotations

import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402

TEAL = "#0E7C66"
TEAL_LIGHT = "#7FBFB0"
GREY = "#C9CED6"
INK = "#1F2933"
MUTED = "#6B7785"
BG = "#FFFFFF"

plt.rcParams.update(
    {
        "font.family": "DejaVu Sans",
        "font.size": 11,
        "axes.edgecolor": "#D5DAE1",
        "axes.labelcolor": INK,
        "text.color": INK,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "figure.facecolor": BG,
        "axes.facecolor": BG,
    }
)

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "assets")
os.makedirs(OUT, exist_ok=True)

# measured in bench (simulated 80ms model, 50ms tool)
TICKET_TOTAL = 6.69
TICKET_PER = TICKET_TOTAL / 24
LANES = 6


def clean(ax):
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.grid(axis="x", color="#EEF1F4", linewidth=1)
    ax.set_axisbelow(True)


def gantt() -> None:
    fig, ax = plt.subplots(figsize=(10, 4.2))

    # baseline: 24 sequential tickets
    for i in range(24):
        ax.add_patch(
            FancyBboxPatch(
                (i * TICKET_PER + 0.01, 1.02),
                TICKET_PER - 0.02,
                0.24,
                boxstyle="round,pad=0,rounding_size=0.012",
                linewidth=0,
                facecolor=GREY,
            )
        )

    # optimized: 6 lanes, 4 tickets each, lanes run concurrently
    for lane in range(LANES):
        y = 0.12 + lane * 0.13
        for j in range(4):
            ax.add_patch(
                FancyBboxPatch(
                    (j * TICKET_PER + 0.01, y),
                    TICKET_PER - 0.02,
                    0.095,
                    boxstyle="round,pad=0,rounding_size=0.010",
                    linewidth=0,
                    facecolor=TEAL if j % 2 == 0 else TEAL_LIGHT,
                )
            )

    ax.axvline(TICKET_PER * 4, color=TEAL, linestyle=(0, (4, 3)), linewidth=1.4, alpha=0.9)
    ax.annotate(
        "6.0x faster",
        xy=(TICKET_PER * 4, 1.42),
        xytext=(TICKET_PER * 4 + 0.25, 1.5),
        color=TEAL,
        fontsize=12,
        fontweight="bold",
    )
    ax.text(TICKET_TOTAL + 0.12, 1.14, "6.69 s  serial", va="center", color=MUTED, fontsize=11)
    ax.text(TICKET_PER * 4 + 0.12, 0.62, "1.11 s  6 lanes", va="center", color=TEAL, fontsize=11, fontweight="bold")

    ax.set_xlim(0, 8.4)
    ax.set_ylim(-0.05, 1.75)
    ax.set_yticks([1.14, 0.52])
    ax.set_yticklabels(["baseline", "loopkit"], fontsize=11)
    ax.set_xlabel("wall clock (s)", fontsize=11)
    ax.set_title("24 support tickets: same work, different orchestration", loc="left", fontsize=13, fontweight="bold", pad=14)
    clean(ax)
    ax.grid(axis="y", visible=False)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "hero-gantt.png"), dpi=200)
    plt.close(fig)


def latency() -> None:
    labels = ["12 docs", "24 tickets", "bugfix loop"]
    base = [2.24, 6.69, 0.55]
    opt = [0.38, 1.11, 0.38]
    speed = [b / o for b, o in zip(base, opt)]

    x = range(len(labels))
    w = 0.34
    fig, ax = plt.subplots(figsize=(9, 4))
    b1 = ax.bar([i - w / 2 for i in x], base, width=w, color=GREY, label="baseline")
    b2 = ax.bar([i + w / 2 for i in x], opt, width=w, color=TEAL, label="loopkit")

    for i, (b, o, s) in enumerate(zip(base, opt, speed)):
        ax.text(i - w / 2, b + 0.12, f"{b:.2f}s", ha="center", color=MUTED, fontsize=10)
        ax.text(i + w / 2, o + 0.12, f"{o:.2f}s", ha="center", color=TEAL, fontsize=10, fontweight="bold")
        ax.text(i, max(b, o) + 0.62, f"{s:.1f}x", ha="center", color=INK, fontsize=14, fontweight="bold")

    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, fontsize=11)
    ax.set_ylabel("wall clock (s)", fontsize=11)
    ax.set_ylim(0, 8.2)
    ax.set_title("Baseline vs loopkit (simulated latency)", loc="left", fontsize=13, fontweight="bold", pad=14)
    ax.legend(frameon=False, loc="upper right")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.grid(axis="y", color="#EEF1F4", linewidth=1)
    ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "perf-latency.png"), dpi=200)
    plt.close(fig)


def tokens() -> None:
    labels = ["12 docs", "24 tickets", "bugfix loop"]
    change = [-38, -75, -30]
    opt_pct = [100 + c for c in change]

    fig, ax = plt.subplots(figsize=(9, 3.8))
    y = list(range(len(labels)))[::-1]
    h = 0.34
    ax.barh([yi + h / 2 for yi in y], [100] * 3, color=GREY, height=h, label="baseline (100%)")
    ax.barh([yi - h / 2 for yi in y], opt_pct, color=TEAL, height=h, label="loopkit")

    for yi, c, o in zip(y, change, opt_pct):
        ax.text(101.5, yi + h / 2, "100%", va="center", color=MUTED, fontsize=10)
        ax.text(o + 1.5, yi - h / 2, f"{o}%", va="center", color=TEAL, fontsize=10, fontweight="bold")
        ax.text(118, yi, f"{c}%", va="center", color=INK, fontsize=12, fontweight="bold")

    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=11)
    ax.set_xlim(0, 132)
    ax.set_xticks([0, 25, 50, 75, 100])
    ax.set_xlabel("input tokens, % of baseline", fontsize=11)
    ax.set_title("Context sent per run: narrower tools and isolated workers cut tokens", loc="left", fontsize=12.5, fontweight="bold", pad=34)
    ax.legend(frameon=False, loc="lower left", bbox_to_anchor=(0.0, 1.02), ncols=2, fontsize=10)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.grid(axis="x", color="#EEF1F4", linewidth=1)
    ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "perf-tokens.png"), dpi=200)
    plt.close(fig)


if __name__ == "__main__":
    gantt()
    latency()
    tokens()
    for name in ("hero-gantt.png", "perf-latency.png", "perf-tokens.png"):
        path = os.path.join(OUT, name)
        print(f"{name:22} {os.path.getsize(path) / 1024:6.1f} KB")
