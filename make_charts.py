"""
Charts for the PDF report.

Palette: dataviz reference instance, light mode (a PDF has one surface, so only
the light column is used). Categorical slots assigned in fixed order - blue is
slot 1, orange slot 2 - never cycled. Validated:
    node scripts/validate_palette.js "#2a78d6,#eb6834" --mode light  -> ALL PASS

Each chart is a magnitude or change-over-time job, so marks are thin, grid is
recessive, and labels are selective rather than on every point.
"""

from __future__ import annotations

import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK2 = "#52514e"
MUTED = "#8a8983"
S1 = "#2a78d6"   # categorical slot 1
S2 = "#eb6834"   # categorical slot 2
GRID = "#e3e2dd"

OUT = "reports/figs"
os.makedirs(OUT, exist_ok=True)

plt.rcParams.update({
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
    "savefig.facecolor": SURFACE,
    "text.color": INK,
    "axes.labelcolor": INK2,
    "xtick.color": INK2,
    "ytick.color": INK2,
    "font.size": 9,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.edgecolor": GRID,
    "grid.color": GRID,
    "grid.linewidth": 0.6,
})


def _finish(ax, title, sub=None, ylab=None):
    """Title and subtitle both drawn as axes-relative text, stacked with explicit
    offsets. ax.set_title(pad=...) collides with a separately-placed subtitle."""
    ax.text(0, 1.16 if sub else 1.04, title, transform=ax.transAxes,
            fontsize=11, fontweight="bold", color=INK, va="bottom")
    if sub:
        ax.text(0, 1.045, sub, transform=ax.transAxes, fontsize=8.5, color=INK2, va="bottom")
    if ylab:
        ax.set_ylabel(ylab, fontsize=8.5)
    ax.tick_params(length=0)


def chart_baselines(path=f"{OUT}/baselines.png"):
    d = pd.read_csv("reports/baselines.csv")
    d = d.sort_values("p@50")
    lbl = {
        "MODEL T2_6m": "Hazard model (T2, 6m)",
        "2_largest": "Largest 100 by size",
        "6_promoter_declining": "Promoter stake declining",
        "3_recent_listing": "Listed in last 24m",
        "5_lockin_90d": "Lock-in expiring in 90d",
        "5b_lockin_promoter_90d": "Promoter lock-in in 90d",
        "4_confirmatory_disclosure": "Trading plan / OFS filed",
        "1_random": "Random selection",
    }
    d["name"] = d["model"].map(lbl).fillna(d["model"])
    colors = [S2 if m.startswith("MODEL") else S1 for m in d["model"]]

    fig, ax = plt.subplots(figsize=(7.2, 3.4), dpi=200)
    y = np.arange(len(d))
    ax.barh(y, d["p@50"], height=0.62, color=colors)
    ax.set_yticks(y); ax.set_yticklabels(d["name"], fontsize=8.5)
    ax.xaxis.grid(True); ax.set_axisbelow(True)
    ax.set_xlim(0, max(d["p@50"]) * 1.18)
    for yi, v in zip(y, d["p@50"]):
        ax.text(v + 0.006, yi, f"{v:.3f}", va="center", fontsize=8, color=INK2)
    base = d["base_rate"].iloc[0]
    ax.axvline(base, color=MUTED, lw=1, ls=(0, (4, 3)))
    ax.text(base, len(d) - 0.35, f"  base rate {base:.3f}", fontsize=7.5, color=MUTED, va="top")
    _finish(ax, "Precision@50 — model vs the five mandated baselines",
            "Share of the top 50 names each month that had a cash event within 6 months. Higher is better.")
    fig.tight_layout(); fig.savefig(path, bbox_inches="tight"); plt.close(fig)
    return path


def chart_h8(path=f"{OUT}/h8_decay.png"):
    d = pd.read_csv("reports/H8_hazard_by_recency.csv")
    lab = [b.replace("[", "").replace(")", "").replace(", ", "–") for b in d["bin"]]
    lab = [l.replace("–10000", "+") for l in lab]
    fig, ax = plt.subplots(figsize=(7.2, 3.2), dpi=200)
    x = np.arange(len(d))
    above = d["lift_vs_never_sold"] >= 1
    ax.bar(x, d["lift_vs_never_sold"], width=0.62,
           color=[S1 if a else S2 for a in above])
    ax.axhline(1.0, color=MUTED, lw=1.2)
    # far right, above the line: the last two bars fall below 1.0, so this is
    # the only region with neither a bar nor a value label in it
    ax.text(8.45, 1.32, "never-sold baseline", fontsize=7.5, color=MUTED, ha="right")
    ax.set_xticks(x); ax.set_xticklabels(lab, fontsize=8)
    ax.set_xlabel("Months since that holder's last sale", fontsize=8.5)
    ax.yaxis.grid(True); ax.set_axisbelow(True)
    for xi, v in zip(x, d["lift_vs_never_sold"]):
        ax.text(xi, v + 0.08, f"{v:.2f}×", ha="center", fontsize=7.5, color=INK2)
    _finish(ax, "H8 is a recency signal, not a standing attribute",
            "Event rate relative to companies that never sold. Below 1.0 means LESS likely than a never-seller.",
            "lift vs never-sold")
    ax.set_ylim(0, 4.5)
    fig.tight_layout(); fig.savefig(path, bbox_inches="tight"); plt.close(fig)
    return path


def chart_calibration(path=f"{OUT}/calibration.png"):
    d = pd.read_csv("reports/calibration.csv")
    fig, ax = plt.subplots(figsize=(4.6, 3.4), dpi=200)
    lim = max(d["predicted"].max(), d["realised"].max()) * 1.1
    ax.plot([0, lim], [0, lim], color=MUTED, lw=1, ls=(0, (4, 3)), zorder=1)
    ax.text(lim * 0.97, lim * 0.9, "perfect\ncalibration", fontsize=7.5, color=MUTED, ha="right")
    ax.plot(d["predicted"], d["realised"], color=S1, lw=2, marker="o", ms=5,
            markerfacecolor=S1, markeredgecolor=SURFACE, markeredgewidth=1.4, zorder=3)
    ax.set_xlabel("predicted probability", fontsize=8.5)
    ax.set_ylabel("realised rate", fontsize=8.5)
    ax.grid(True); ax.set_axisbelow(True)
    ax.set_xlim(0, lim); ax.set_ylim(0, lim)
    _finish(ax, "Calibration is poor at the top",
            "Points below the line = over-prediction.")
    fig.tight_layout(); fig.savefig(path, bbox_inches="tight"); plt.close(fig)
    return path


def chart_ranking(path=f"{OUT}/ranking.png"):
    d = pd.read_csv("reports/ranking_objectives.csv")
    lbl = {"rank_prob": "Probability only", "rank_expected_proceeds": "P × size",
           "rank_sqrt_size": "P × √size"}
    d["name"] = d["objective"].map(lbl)
    fig, ax = plt.subplots(figsize=(6.4, 3.0), dpi=200)
    x = np.arange(len(d)); w = 0.36
    # two measures, same 0-1 scale, one axis - legal as grouped bars
    ax.bar(x - w / 2 - 0.01, d["p@50"], width=w, color=S1, label="Hit rate (p@50)")
    ax.bar(x + w / 2 + 0.01, d["wallet_p@50"], width=w, color=S2, label="Rupee capture (wallet p@50)")
    ax.set_xticks(x); ax.set_xticklabels(d["name"], fontsize=8.5)
    ax.yaxis.grid(True); ax.set_axisbelow(True)
    for xi, a, b in zip(x, d["p@50"], d["wallet_p@50"]):
        ax.text(xi - w / 2 - 0.01, a + 0.008, f"{a:.2f}", ha="center", fontsize=7.5, color=INK2)
        ax.text(xi + w / 2 + 0.01, b + 0.008, f"{b:.2f}", ha="center", fontsize=7.5, color=INK2)
    ax.legend(frameon=False, fontsize=8, loc="upper center", ncol=2, bbox_to_anchor=(0.5, 1.02))
    _finish(ax, "The ranking objective decides what you capture",
            "Probability ranking finds more events; P × √size captures 4.8× the rupees.")
    ax.set_ylim(0, 0.52)
    fig.tight_layout(); fig.savefig(path, bbox_inches="tight"); plt.close(fig)
    return path


if __name__ == "__main__":
    for f in (chart_baselines, chart_h8, chart_calibration, chart_ranking):
        print("wrote", f())
