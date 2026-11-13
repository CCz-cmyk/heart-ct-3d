# -*- coding: utf-8 -*-
"""
把实验结果画成 4 张图（中文标签，300 dpi），供报告与 PPT 直接引用。
输出：research/figures/*.png
"""

import csv
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "research" / "results"
FIG = ROOT / "research" / "figures"
FIG.mkdir(parents=True, exist_ok=True)

for name in ("SimHei", "Microsoft YaHei", "SimSun"):
    try:
        font_manager.findfont(font_manager.FontProperties(family=name), fallback_to_default=False)
        plt.rcParams["font.sans-serif"] = [name]
        break
    except Exception:
        continue
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["figure.dpi"] = 110

COLORS = {1: "#c0392b", 2: "#2471a3"}
LABELS = {1: "病灶1 前降支钙化斑块 (r=12)", 2: "病灶2 右冠混合斑块 (r=10)"}


def load_summary():
    rows = []
    with open(RES / "experiment_summary.csv", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append({
                "lesion": int(r["lesion_id"]),
                "t": int(r["slice_thickness"]),
                "th": int(r["threshold_hu"]),
                "off": int(r["offset_nominal"]),
                "err": float(r["rel_error_mean_pct"]),
                "err_sd": float(r["rel_error_sd_pct"]),
                "cv": float(r["repeatability_cv_pct"]),
            })
    return rows


def panel_error(ax, rows, key, fixed, xlabel, title):
    for lid in (1, 2):
        sel = [r for r in rows if r["lesion"] == lid and all(r[k] == v for k, v in fixed.items())]
        sel.sort(key=lambda r: r[key])
        xs = [r[key] for r in sel]
        ys = [r["err"] for r in sel]
        ax.errorbar(xs, ys, yerr=[r["err_sd"] for r in sel], marker="o", ms=5, lw=1.8,
                    capsize=3, color=COLORS[lid], label=LABELS[lid])
    ax.axhline(0, color="#555555", lw=1, ls="--")
    ax.set_xlabel(xlabel)
    ax.set_ylabel("相对误差 (%)")
    ax.set_title(title)
    if key == "t":
        ax.set_xticks([1, 2, 3, 4, 5])
    elif key == "th":
        ax.set_xticks([150, 200, 250, 300, 350])
    elif key == "off":
        ax.set_xticks([-6, -4, -2, 0, 2, 4, 6])
    ax.grid(alpha=0.3, ls="-")
    ax.legend(fontsize=8, loc="best")


def fig_layout(n=2):
    fig, axes = plt.subplots(1, n, figsize=(11.5, 4.2), constrained_layout=True)
    return fig, axes


def main():
    rows = load_summary()

    # 图1 误差 vs 剖切位置
    fig, axes = fig_layout()
    panel_error(axes[0], rows, "off", {"t": 1, "th": 200}, "剖切起点偏移 (层)", "(a) 层厚 1 / 阈值 200")
    panel_error(axes[1], rows, "off", {"t": 3, "th": 200}, "剖切起点偏移 (层)", "(b) 层厚 3 / 阈值 200")
    fig.suptitle("图1  剖切位置偏移对病灶体积测量误差的影响", fontsize=13)
    fig.savefig(FIG / "fig1_error_vs_offset.png", dpi=220)
    plt.close(fig)

    # 图2 误差 vs 层厚
    fig, axes = fig_layout()
    panel_error(axes[0], rows, "t", {"off": 0, "th": 200}, "层厚 (层)", "(a) 阈值 200 / 偏移 0")
    panel_error(axes[1], rows, "t", {"off": 0, "th": 250}, "层厚 (层)", "(b) 阈值 250 / 偏移 0")
    fig.suptitle("图2  重建层厚对病灶体积测量误差的影响", fontsize=13)
    fig.savefig(FIG / "fig2_error_vs_thickness.png", dpi=220)
    plt.close(fig)

    # 图3 误差 vs 阈值
    fig, axes = fig_layout()
    panel_error(axes[0], rows, "th", {"t": 1, "off": 0}, "分割阈值 (HU)", "(a) 层厚 1 / 偏移 0")
    panel_error(axes[1], rows, "th", {"t": 3, "off": 0}, "分割阈值 (HU)", "(b) 层厚 3 / 偏移 0")
    axes[1].axvline(271, color="#999999", lw=1, ls=":")
    axes[1].text(275, -80, "病灶2 峰值 271 HU", fontsize=7.5, color="#666666")
    fig.suptitle("图3  分割阈值对病灶体积测量误差的影响", fontsize=13)
    fig.savefig(FIG / "fig3_error_vs_threshold.png", dpi=220)
    plt.close(fig)

    # 图4 AI 参数 vs 默认参数
    ai_rows = []
    with open(RES / "ai_vs_default.csv", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            ai_rows.append({
                "lesion": int(r["lesion_id"]),
                "src": r["param_source"],
                "err": float(r["rel_error_pct"]),
                "cv": float(r["repeatability_cv_pct"]),
                "t": int(r["slice_thickness_mm"]),
                "th": int(r["threshold_hu"]),
            })

    src_order = [("default", "临床默认"), ("ai", "AI 推荐"), ("grid_best", "穷举最优")]
    src_color = {"default": "#8d8d8d", "ai": "#1e8449", "grid_best": "#2e86c1"}
    fig, ax = plt.subplots(figsize=(8.6, 4.4), constrained_layout=True)
    width = 0.26
    xs = np.arange(2)
    for i, (src, label) in enumerate(src_order):
        vals, cvs, notes = [], [], []
        for lid in (1, 2):
            hit = [r for r in ai_rows if r["lesion"] == lid and r["src"] == src]
            vals.append(hit[0]["err"] if hit else 0.0)
            cvs.append(hit[0]["cv"] if hit else 0.0)
            notes.append("t=%d,th=%d" % (hit[0]["t"], hit[0]["th"]) if hit else "")
        bars = ax.bar(xs + (i - 1) * width, vals, width, color=src_color[src],
                      label=label, edgecolor="white", lw=0.8)
        for b, v, n in zip(bars, vals, notes):
            ax.annotate("%+.2f%%\n%s" % (v, n), (b.get_x() + b.get_width() / 2, v),
                        ha="center", va="bottom" if v >= 0 else "top",
                        fontsize=7.5, xytext=(0, 3 if v >= 0 else -3),
                        textcoords="offset points")
    ax.axhline(0, color="#555555", lw=1, ls="--")
    ax.set_xticks(xs)
    ax.set_xticklabels([LABELS[1], LABELS[2]], fontsize=9)
    ax.set_ylabel("相对误差 (%)")
    ax.set_title("图4  AI 推荐参数与临床默认参数的测量误差对比", fontsize=13)
    ax.grid(axis="y", alpha=0.3)
    ax.legend(fontsize=9)
    all_err = [r["err"] for r in ai_rows]
    lo, hi = min(all_err), max(all_err)
    ax.set_ylim(lo - 2.0, hi + 2.2)
    fig.savefig(FIG / "fig4_ai_vs_default.png", dpi=220)
    plt.close(fig)

    for p in sorted(FIG.glob("*.png")):
        print("OUT %s  %.0f KB" % (p.name, p.stat().st_size / 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
