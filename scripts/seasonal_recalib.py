"""生活垃圾实测锚点回填 → 原生垃圾收益/混合热值/协同价值 重估（V4 10/13/14 表口径）。

用法：python scripts/seasonal_recalib.py
依据：2025 年 7~9 月一期/二期生活垃圾厂内自检实测（assays_2025.csv 已入库 pricing.MSW_ASSAY）。
输出：控制台报告 + data/assays/seasonal_recalib.csv
"""
from __future__ import annotations

import csv
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hhv.pricing import (MSW_ASSAY, BatchInput, batch_economics, fixed_cost_per_t,  # noqa: E402
                         load_plant_slag, mix_heat, msw_q, phase_bias, portfolio,
                         feasibility_native, shutdown_economics)

# 实测工业固废批次（最新一份化验；吨数沿用 V4 算例，处置费沿用 V4 算例价）
REAL_BATCHES = [
    # (品类, 供应商, 月, q_lab, moisture, ash, daily_t, disposal)
    ("造纸(其它)", "理文", 2, 1626.0, 57.68, 19.03, 150, 80),
    ("建装筛上物", "天越", 7, 4438.0, 30.31, 13.43, 180, 100),
    ("农林", "雷博尔", 3, 2792.0, 33.40, 11.66, 130, 60),
]


def main() -> int:
    bias = phase_bias()
    fixed_pt = fixed_cost_per_t(2560.0, 330.0)

    # 实测工业固废组合
    plant = load_plant_slag()
    slag_kw = {"slag_rate_pct": plant["rate_pct"]} if plant else None
    native_slag = plant["rate_pct"] if plant else 22.0
    batches = [BatchInput(name=f"{c}·实测", q_lab=q, moisture_lab=m, moisture_furnace=m,
                          ash=a, stock_days=5, blend_pct=20, disposal_price=d,
                          leachate_rate=0.35 if c == "造纸(其它)" else (0.08 if c == "建装筛上物" else 0.10),
                          daily_t=t)
               for c, _, _, q, m, a, t, d in REAL_BATCHES]
    if slag_kw:
        ecoms = [batch_economics(b, fixed_per_t=fixed_pt, **slag_kw) for b in batches]
        port = portfolio(batches, ecoms)
        shut = shutdown_economics(weighted_margin=port["margin"])
    else:
        ecoms, port, shut = None, None, None
        print("无炉渣出厂台账：经济性用 V4 占位渣率 22% 仅作 10 表对照，不定价")

    print("=" * 84)
    print("一、一期 vs 二期生活垃圾同月偏差（2025 实测）——二期系统性偏高")
    print("=" * 84)
    for m, d in bias["per_month"].items():
        p1, p2 = MSW_ASSAY[m]
        print(f"  {m}月: 一期 {p1:.0f} vs 二期 {p2:.0f} kcal/kg → 二期偏高 {d:+.1%}")
    print(f"  均值偏差 {bias['mean']:+.1%} → 定价引擎 MSW 成分采用二期实测；一期锚仅作交叉验证")

    print()
    print("=" * 84)
    print("二、生活垃圾热值月历（二期口径，kcal/kg 湿基低位）vs V4 静态假设 1600")
    print("=" * 84)
    curve = {}
    for m in range(1, 13):
        q, flag = msw_q(m)
        curve[m] = q
        mark = "实测" if flag == "measured" else "占位"
        print(f"  {m:>2}月: {q:>7.0f}  [{mark}]")
    print("  V4 静态 1600 ↔ 实测 7~9 月均值 "
          f"{sum(v[1] for v in MSW_ASSAY.values()) / 3:.0f}（V4 低估 {(1 - 1600 / (sum(v[1] for v in MSW_ASSAY.values()) / 3)) * 100:.0f}%），"
          "且无季节项（8→9 月 +50~60%）")

    print()
    print("=" * 84)
    print("三、原生垃圾基准吨收益（V4 10 表口径，处置费 100 元/t）按月重估")
    print("=" * 84)
    f_rows = []
    for m in range(1, 13):
        q, flag = msw_q(m)
        f = feasibility_native(q, slag_rate_pct=native_slag)
        f_v4 = feasibility_native(1600.0, slag_rate_pct=native_slag)
        f_rows.append({"month": m, "q_msw_p2": round(q), "flag": flag,
                       "native_margin": round(f["native_margin"], 1),
                       "native_margin_v4_1600": round(f_v4["native_margin"], 1)})
        print(f"  {m:>2}月: Q={q:>7.0f} → 原生吨收益 {f['native_margin']:>7.1f} 元/t"
              f"   (V4@1600: {f_v4['native_margin']:.1f})  [{flag}]")

    print()
    print("=" * 84)
    print("四、混合热值月度校验（V4 14 表：原生 2100 t/d + 市场化 460 t/d）")
    print("=" * 84)
    if port is None:
        wsum = sum(b.daily_t for b in batches)
        q_market = sum(b.daily_t * b.q_lab * (0.94 if b.stock_days > 3 else 0.97)
                       * (0.97 if b.ash > 30 else 1.0) for b in batches) / wsum
    else:
        q_market = port["q_in"]
    print(f"  实测市场化垃圾加权热值 {q_market:.0f} kcal/kg（含水取化验值、k_moist=1）")
    for m in (7, 8, 9):
        q, _ = msw_q(m)
        mix = mix_heat(2100.0, q, 460.0, q_market)
        print(f"  {m}月: Q生活={q:.0f} → 混合入炉热值 {mix:.0f} kcal/kg"
              f"（V4@1600: {mix_heat(2100.0, 1600.0, 460.0, q_market):.0f}）")

    print()
    print("=" * 84)
    print("五、协同增量总价值重估（实测工业固废热值口径）")
    print("=" * 84)
    if shut is None or port is None:
        print("  跳过：填写 data/plant/slag_outbound.csv 后按实际炉渣率重算")
    else:
        print(f"  炉渣率 {plant['rate_pct']:.2f}%（出厂 {plant['slag_out_t']:.0f} / 入炉 {plant['furnace_input_t']:.0f}）")
        print(f"  实测加权边际贡献 {port['margin']:.1f} 元/t（V4 算例口径 186.0）")
        print(f"  协同固废增量边际 {shut['increment_margin']:.0f} 万元/年"
              f"（V4 算例口径 2825）")
        print(f"  避免停炉损失 {shut['avoided_loss']:.0f} 万元/年（与热值无关，不变）")
        print(f"  协同增量总价值 {shut['synergy_total']:.0f} 万元/年（V4 算例口径 3646）")

    out = ROOT / "data" / "assays" / "seasonal_recalib.csv"
    with open(out, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["month", "q_msw_phase2_kcal", "source", "native_margin_yuan_t",
                    "native_margin_v4_at_1600", "mix_heat_kcal"])
        for m in range(1, 13):
            q, flag = msw_q(m)
            mix = mix_heat(2100.0, q, 460.0, q_market)
            f = feasibility_native(q, slag_rate_pct=native_slag)
            w.writerow([m, round(q), flag, round(f["native_margin"], 1),
                        round(feasibility_native(1600.0, slag_rate_pct=native_slag)["native_margin"], 1),
                        round(mix)])
    print(f"\nwritten: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
