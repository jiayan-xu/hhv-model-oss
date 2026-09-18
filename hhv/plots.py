"""诊断图：周序列、回归拟合、筛选覆盖、闸门对比。"""
from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

ACCENT = "#D4875A"
INK = "#1A2330"
GRAY = "#90989F"


def fig_weekly(weekly, path):
    fig, axes = plt.subplots(2, 1, figsize=(10, 6.5), sharex=True)
    w = weekly["week"]
    axes[0].plot(w, weekly["qv_p1"], "o-", color=INK, label="一期 Q生活（锚）")
    axes[0].plot(w, weekly["qv_p2"], "s-", color=ACCENT, label="二期 Q混（掺烧）")
    axes[0].set_ylabel("热值 kJ/kg")
    axes[0].legend()
    axes[0].grid(alpha=0.3)
    axes[1].bar(w, weekly["alpha_total"], color=ACCENT, alpha=0.8)
    axes[1].set_ylabel("总掺烧比例 α")
    axes[1].grid(alpha=0.3)
    plt.xticks(rotation=60, fontsize=8)
    fig.suptitle("周尺度热值序列与掺烧比例")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def fig_regression(qmix, alpha_total, fit, path):
    fig, ax = plt.subplots(figsize=(7, 5))
    ax.scatter(alpha_total, qmix, s=28, color=INK, label="周样本")
    xs = np.linspace(alpha_total.min(), alpha_total.max(), 50)
    b0, b1 = fit["beta"][0], fit["beta"][1:].sum()  # 总斜率（多来源合并展示）
    ax.plot(xs, b0 + b1 * xs, color=ACCENT, lw=2,
            label=f"Q混 = {b0:.0f} + {b1:.0f}×α")
    ax.set_xlabel("总掺烧比例 α")
    ax.set_ylabel("混合热值 kJ/kg")
    ax.set_title(f"回归分解（Huber）：Q生活={b0:.0f}，Q工业={b0 + b1:.0f}")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def fig_screening(day1, day2, path):
    fig, axes = plt.subplots(2, 1, figsize=(10, 5.5), sharex=False)
    for ax, day, tag in ((axes[0], day1, "一期"), (axes[1], day2, "二期")):
        ok = day[day["day_valid"]]
        bad = day[~day["day_valid"]]
        ax.plot(ok.index, ok["steam_flow"], "o", ms=2.5, color=INK, label="有效日")
        ax.plot(bad.index, bad["steam_flow"], "x", ms=4, color="#C0392B", label="剔除日")
        ax.set_ylabel(f"{tag} 主汽流量 t/h")
        ax.legend(loc="upper right", fontsize=8)
        ax.grid(alpha=0.3)
    fig.suptitle("日级自动筛选结果（×为剔除日）")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def fig_gate(detail, path):
    if detail is None:
        return
    fig, ax = plt.subplots(figsize=(7, 4.5))
    x = np.arange(len(detail))
    ax.bar(x - 0.18, detail["qv_hist"], 0.36, color=GRAY, label="二期两年前（历史）")
    ax.bar(x + 0.18, detail["qv_now"], 0.36, color=ACCENT, label="一期（当期）")
    ax.set_xticks(x)
    ax.set_xticklabels([f"{int(m)}月" for m in detail["month"]])
    ax.set_ylabel("Q生活 月均 kJ/kg")
    ax.set_title("交叉验证闸门：历史 vs 当期")
    ax.legend()
    ax.grid(alpha=0.3, axis="y")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
