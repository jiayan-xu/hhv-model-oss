# -*- coding: utf-8 -*-
"""月度餐厨占比、厂内热值闸门、踢掉 |残差|>0.25 周后的回归影响。"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "snmis_raw"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from diagnose_aug_mass import parse_extra  # noqa: E402


def main():
    rows = []
    for p in sorted(RAW.glob("dailyReport01_2026-*.xlsx")):
        y, m = map(int, p.stem.split("_")[1].split("-"))
        df = parse_extra(p, y, m)
        kit = df.p1_kitchen.sum()
        pin = df.p1_in.sum()
        rows.append({
            "ym": f"{y}-{m:02d}",
            "p1_in": pin, "kitchen": kit, "kit_frac": kit / pin if pin else None,
            "p1_q": df.p1_q.mean(), "p2_q": df.p2_q.mean(),
            "p1_ratio": df.p1_steam_ratio.mean(), "p2_ratio": df.p2_steam_ratio.mean(),
            "p1_furn": df.p1_furnace.mean(), "p2_furn": df.p2_furnace.mean(),
        })
    print("=== 2026 月度 ===")
    print(pd.DataFrame(rows).round(3).to_string(index=False))

    rows24 = []
    for p in sorted(RAW.glob("dailyReport01_2024-*.xlsx")):
        y, m = map(int, p.stem.split("_")[1].split("-"))
        df = parse_extra(p, y, m)
        kit = df.p1_kitchen.sum()
        pin = df.p1_in.sum()
        rows24.append({
            "ym": f"{y}-{m:02d}",
            "kit_frac": kit / pin if pin else None,
            "p1_q": df.p1_q.mean(), "p2_q": df.p2_q.mean(),
            "p1_ratio": df.p1_steam_ratio.mean(), "p2_ratio": df.p2_steam_ratio.mean(),
        })
    print("\n=== 2024 月度 ===")
    print(pd.DataFrame(rows24).round(3).to_string(index=False))

    # plant_q k-gate
    r26 = pd.DataFrame(rows).set_index(pd.Series([int(x.split("-")[1]) for x in [r["ym"] for r in rows]]))
    # rebuild with month index
    m26 = {int(r["ym"].split("-")[1]): r for r in rows}
    m24 = {int(r["ym"].split("-")[1]): r for r in rows24}
    print("\n=== 厂内热值月闸门 (2024 p2 vs 2026 p1) ===")
    deltas = []
    ks = []
    for m in sorted(set(m26) & set(m24)):
        h, n = m24[m]["p2_q"], m26[m]["p1_q"]
        d = abs(h - n) / n
        k = h / n
        deltas.append(d)
        ks.append(k)
        print(f"  {m:2d}月  hist={h:.0f} now={n:.0f} delta={d:.1%} k={k:.3f}  "
              f"kit26={m26[m]['kit_frac']:.1%} kit24={m24[m]['kit_frac']:.1%}  "
              f"ratio p1_26={m26[m]['p1_ratio']:.2f} p2_24={m24[m]['p2_ratio']:.2f}")
    import numpy as np
    print(f"plant_q delta_mean={np.mean(deltas):.1%} median_k={np.median(ks):.4f}")
    print(f"trim worst delta_mean={np.mean(sorted(deltas)[:-1]):.1%}")

    w = pd.read_csv(ROOT / "outputs/config.snmis_daily/weekly_series.csv")
    print("\n=== |balance_rel| 周分布 ===")
    print(w.balance_rel.abs().describe().round(3).to_string())
    for thr in [0.20, 0.25, 0.30]:
        drop = w[w.balance_rel.abs() > thr]
        keep = w[w.balance_rel.abs() <= thr]
        print(f"thr {thr:.2f}: drop {len(drop)} keep {len(keep)} drop_weeks={list(drop.week)}")


if __name__ == "__main__":
    main()
