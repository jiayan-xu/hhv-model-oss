# -*- coding: utf-8 -*-
"""拆 8 月 k 差 + 物料闭合。只读本地 CSV/xlsx/dashboard.db，不登 SNMIS。"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "snmis_raw"
PLANT = ROOT / "data" / "plant"
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _local_env import dashboard_db  # noqa: E402
DB = dashboard_db()
sys.path.insert(0, str(ROOT))

from hhv.masses import weekly_masses  # noqa: E402


def _num(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _day_cols(ws):
    out = []
    for col in range(6, ws.max_column + 1):
        h = ws.cell(2, col).value
        if h is None:
            continue
        s = str(h).replace("日", "").strip()
        if s.isdigit():
            out.append((col, int(s)))
    return out


def dump_labels(path: Path):
    wb = load_workbook(path, data_only=True)
    print("=== labels", path.name, "===")
    for sn in wb.sheetnames[:2]:
        ws = wb[sn]
        print("--", sn)
        for r in range(1, 10):
            print(r, ws.cell(r, 2).value, ws.cell(r, 3).value)


def parse_extra(path: Path, year: int, month: int) -> pd.DataFrame:
    wb = load_workbook(path, data_only=True)
    names = wb.sheetnames
    p1, p2 = wb[names[0]], wb[names[1]]
    rows = []
    for col, d in _day_cols(p1):
        try:
            ts = pd.Timestamp(year=year, month=month, day=d)
        except ValueError:
            continue
        rows.append({
            "date": ts,
            "p1_in": _num(p1.cell(4, col).value),
            "p1_kitchen": _num(p1.cell(5, col).value),
            "p1_furnace": _num(p1.cell(6, col).value),
            "p1_pit": _num(p1.cell(7, col).value),
            "p1_q": _num(p1.cell(25, col).value),
            "p1_steam_ratio": _num(p1.cell(22, col).value),
            "p2_in": _num(p2.cell(4, col).value),
            "p2_furnace": _num(p2.cell(5, col).value),
            "p2_pit": _num(p2.cell(6, col).value),
            "p2_q": _num(p2.cell(25, col).value),
            "p2_steam_ratio": _num(p2.cell(22, col).value),
        })
    return pd.DataFrame(rows)


def load_isw_detail() -> pd.DataFrame:
    con = sqlite3.connect(DB)
    df = pd.read_sql_query(
        "SELECT entrance_date, waste_type, company_name, "
        "COALESCE(net_weight, weight, 0) AS w FROM vehicle_entrance",
        con,
    )
    con.close()
    df["entrance_date"] = pd.to_datetime(df["entrance_date"], errors="coerce")
    df = df.dropna(subset=["entrance_date"])
    df["date"] = df["entrance_date"].dt.normalize()
    return df


def monthly_block(df: pd.DataFrame, col: str) -> pd.Series:
    x = df.set_index("date")[col]
    return x.groupby(x.index.month).mean()


def main():
    dump_labels(RAW / "dailyReport01_2026-08.xlsx")
    dump_labels(RAW / "dailyReport01_2024-08.xlsx")

    cur = parse_extra(RAW / "dailyReport01_2026-08.xlsx", 2026, 8)
    hist = parse_extra(RAW / "dailyReport01_2024-08.xlsx", 2024, 8)
    print("\n=== 8月生产日报（月合计/日均）===")
    for name, df in [("2026", cur), ("2024", hist)]:
        print(name, "days", len(df))
        for c in ["p1_in", "p1_kitchen", "p1_furnace", "p1_q", "p1_steam_ratio",
                  "p2_in", "p2_furnace", "p2_q", "p2_steam_ratio", "p1_pit", "p2_pit"]:
            if c in df and df[c].notna().any():
                s = df[c]
                print(f"  {c:16s} sum={s.sum():10.1f} mean={s.mean():8.1f} "
                      f"min={s.min():8.1f} max={s.max():8.1f} nan={int(s.isna().sum())}")

    p1 = pd.read_csv(PLANT / "phase1_daily.csv", parse_dates=["time"])
    p2 = pd.read_csv(PLANT / "phase2_daily.csv", parse_dates=["time"])
    p2h = pd.read_csv(PLANT / "phase2_hist_daily.csv", parse_dates=["time"])
    led = pd.read_csv(PLANT / "ledger_daily.csv", parse_dates=["date"])
    p1a = p1[(p1.time >= "2026-08-01") & (p1.time < "2026-09-01")]
    p2a = p2[(p2.time >= "2026-08-01") & (p2.time < "2026-09-01")]
    p2ha = p2h[(p2h.time >= "2024-08-01") & (p2h.time < "2024-09-01")]
    print("\n=== 8月 CSV 蒸汽/温压 ===")
    for name, df in [("p1_2026", p1a), ("p2_2026", p2a), ("p2_2024", p2ha)]:
        print(name, "n", len(df),
              "flow_mean", round(df.steam_flow.mean(), 2),
              "T", round(df.steam_t.mean(), 1),
              "P", round(df.steam_p.mean(), 3),
              "o2", round(df.o2.mean(), 2),
              "fg", round(df.fluegas_t.mean(), 1),
              "fw", round(df.feedwater_t.mean(), 1))

    print("\n=== 厂内热值 vs 模型 8 月 ===")
    print("2026 p1 plant_q mean", round(cur.p1_q.mean(), 1), "model monthly 6088")
    print("2026 p2 plant_q mean", round(cur.p2_q.mean(), 1))
    print("2024 p2 plant_q mean", round(hist.p2_q.mean(), 1), "model monthly 7266")
    print("2024 p1 plant_q mean", round(hist.p1_q.mean(), 1) if hist.p1_q.notna().any() else None)

    # 吨汽差
    print("\n=== 吨垃圾产汽 8月 ===")
    print("2026 p1", round(cur.p1_steam_ratio.mean(), 3), "p2", round(cur.p2_steam_ratio.mean(), 3))
    print("2024 p1", round(hist.p1_steam_ratio.mean(), 3), "p2", round(hist.p2_steam_ratio.mean(), 3))

    isw = load_isw_detail()
    print("\n=== vehicle_entrance 货名 ===")
    g = isw.groupby(isw.waste_type.fillna("(空)"))["w"].agg(["count", "sum"]).sort_values("sum", ascending=False)
    print(g.head(20).to_string())
    a8 = isw[(isw.date >= "2026-08-01") & (isw.date < "2026-09-01")]
    print("2026-08 ISW sum", round(a8.w.sum(), 1), "n", len(a8))
    print(a8.groupby(a8.waste_type.fillna("(空)"))["w"].sum().sort_values(ascending=False).head(15).to_string())

    # 物料闭合变体
    led = led.set_index("date")
    extra = parse_extra(RAW / "dailyReport01_2026-08.xlsx", 2026, 8)
    # 全年 extra
    extras = []
    for p in sorted(RAW.glob("dailyReport01_2026-*.xlsx")):
        y, m = map(int, p.stem.split("_")[1].split("-"))
        extras.append(parse_extra(p, y, m))
    ex = pd.concat(extras, ignore_index=True).set_index("date")
    print("\n=== 入厂 vs 入炉（日历日，无滞后）===")
    plant = ex["p1_in"].fillna(0) + ex["p2_in"].fillna(0)
    furn = ex["p1_furnace"].fillna(0) + ex["p2_furnace"].fillna(0)
    kit = ex["p1_kitchen"].fillna(0)
    print("plant_in sum", round(plant.sum(), 1), "furnace", round(furn.sum(), 1),
          "kitchen", round(kit.sum(), 1),
          "(plant-furn)/furn", round(float((plant - furn).abs().sum() / furn.sum()), 4),
          "(plant-furn)/plant signed", round(float((plant - furn).sum() / plant.sum()), 4))
    isw_d = isw.groupby("date")["w"].sum()
    isw_al = isw_d.reindex(ex.index).fillna(0)
    print("ISW sum overlap", round(isw_al.sum(), 1), "ISW/furnace", round(isw_al.sum() / furn.sum(), 4))
    print("kitchen/p1_in", round(kit.sum() / ex.p1_in.fillna(0).sum(), 4))

    # 若二期入厂已是生活垃圾，减 ISW 会双减
    msw_cur = (ex.p1_in.fillna(0) + ex.p2_in.fillna(0) - isw_al).clip(lower=0)
    msw_nodeduct = ex.p1_in.fillna(0) + ex.p2_in.fillna(0)
    msw_pluskit_nodeduct = msw_nodeduct  # kitchen 已含在 p1_in？看相关
    print("corr kitchen vs p1_in", ex.p1_kitchen.corr(ex.p1_in))
    print("kitchen / p1_in daily mean ratio", (ex.p1_kitchen / ex.p1_in.replace(0, np.nan)).mean())

    def rel(msw, isw_s, lag_m, lag_i):
        m = msw.shift(lag_m)
        i = isw_s.shift(lag_i)
        res = furn - m - i
        return float((res.abs() / furn.replace(0, np.nan)).mean())

    print("\n=== 周均 |残差|/入炉 网格 ===")
    print("variant lag_msw lag_isw rel")
    for name, msw in [
        ("cur=p1+p2-isw", msw_cur),
        ("nodeduct=p1+p2", msw_nodeduct),
        ("p2only+p1", msw_nodeduct),
    ]:
        for lm in [0, 1, 2, 3, 5, 7]:
            for li in [0, 1, 2, 3]:
                r = rel(msw, isw_al, lm, li)
                if r < 0.16:
                    print(f"  {name:18s} {lm} {li} {r:.3f}")

    # 不把 isw 当燃料、只比 furnace vs lag(plant_in)
    print("\n=== 仅入厂滞后 vs 入炉（不计 isw 燃料项）===")
    for lm in range(0, 8):
        m = plant.shift(lm)
        r = float(((furn - m).abs() / furn.replace(0, np.nan)).mean())
        signed = float(((furn - m) / furn.replace(0, np.nan)).mean())
        print(f"  lag {lm}: |rel|={r:.3f} signed={signed:+.3f}")

    print("\n=== 坑存（有数的日子）===")
    print("p1_pit nonzero", int((ex.p1_pit.fillna(0) != 0).sum()), "mean", round(ex.p1_pit.mean(), 1))
    print("p2_pit nonzero", int((ex.p2_pit.fillna(0) != 0).sum()), "mean", round(ex.p2_pit.mean(), 1),
          "min", ex.p2_pit.min(), "max", ex.p2_pit.max())

    # 模型 masses 口径
    print("\n=== 现用 weekly_masses ===")
    w = weekly_masses(led, {"lag_days_msw": 3, "lag_days_isw": 0})
    print("mean |balance_rel|", round(float(w.balance_rel.abs().mean()), 4),
          "median", round(float(w.balance_rel.abs().median()), 4))
    print(w[["phase1_input", "phase2_input", "msw_in", "isw::工业固废", "balance_residual", "balance_rel"]].to_string())

    # 8 月日级：一期负荷
    print("\n=== 8月一期负荷 vs rated 60 ===")
    print("p1 flow/60", round((p1a.steam_flow / 60).mean(), 3),
          "min", round((p1a.steam_flow / 60).min(), 3),
          "days<0.5", int((p1a.steam_flow / 60 < 0.5).sum()),
          "days<0.7", int((p1a.steam_flow / 60 < 0.7).sum()))
    print("p2 flow/160", round((p2a.steam_flow / 160).mean(), 3))
    print("p2h flow/160", round((p2ha.steam_flow / 160).mean(), 3))

    # 7 vs 8 月一期
    p1j = p1[(p1.time >= "2026-07-01") & (p1.time < "2026-08-01")]
    print("7月 p1 flow", round(p1j.steam_flow.mean(), 2), "8月", round(p1a.steam_flow.mean(), 2))
    jul = parse_extra(RAW / "dailyReport01_2026-07.xlsx", 2026, 7)
    print("7月 p1_q", round(jul.p1_q.mean(), 1), "8月 p1_q", round(cur.p1_q.mean(), 1))
    print("7月 p1_ratio", round(jul.p1_steam_ratio.mean(), 3), "8月", round(cur.p1_steam_ratio.mean(), 3))
    print("7月 p1_furnace/d", round(jul.p1_furnace.mean(), 1), "8月", round(cur.p1_furnace.mean(), 1))

    # W34
    print("\n=== 8月逐日 p1_q / steam_ratio / furnace ===")
    x = cur.copy()
    x["dow"] = x.date.dt.dayofweek
    print(x[["date", "p1_q", "p1_steam_ratio", "p1_furnace", "p1_in", "p2_q", "p2_furnace"]].to_string(index=False))


if __name__ == "__main__":
    main()
