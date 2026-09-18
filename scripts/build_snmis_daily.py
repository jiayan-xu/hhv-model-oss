"""把 FineReport 生产日报（日列）+ dashboard 固废进厂 编成 hhv-model 日均值输入。

日报没有主汽温/压、给水温度、氧量、排烟温度：用 2026-09-01 指标日报的分炉均值做常数，
并在 CSV 的 remark 里标明。蒸汽流量 = 三炉产汽量合计 / 运行小时（小时为 0 则 /24）。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _local_env import dashboard_db  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "snmis_raw"
OUT = ROOT / "data" / "plant"
DB = dashboard_db()

# 2026-09-01 运行指标日报分炉均值（一期 #1-3 / 二期 #4-6）
TYP = {
    "p1": dict(steam_p=3.89, steam_t=400.68, feedwater_t=135.74, o2=5.18, fluegas_t=213.54),
    "p2": dict(steam_p=3.88, steam_t=400.87, feedwater_t=131.35, o2=3.84, fluegas_t=228.17),
}

# 行号（B 列标签编码损坏，结构稳定）：见 dailyReport01
# 4 入厂  5 餐厨/二期无  6 入炉（一期） / 二期 row4=入厂 row5=入炉
# 25 垃圾热值
# 炉块：1# 从 26，每炉 13 行（标题+12 指标），指标：小时/焚烧/机械/产汽/给水/热负荷...
P1_FURNACE_STARTS = (26, 39, 52)
P2_FURNACE_STARTS = (26, 39, 52)


def _num(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _day_cols(ws) -> list[tuple[int, int]]:
    """(excel_col, day_of_month) 从第 2 行表头 1日.."""
    out = []
    for col in range(6, ws.max_column + 1):
        h = ws.cell(2, col).value
        if h is None:
            continue
        s = str(h).replace("日", "").strip()
        if s.isdigit():
            out.append((col, int(s)))
    return out


def parse_phase_sheet(ws, year: int, month: int, phase: str) -> pd.DataFrame:
    days = _day_cols(ws)
    # 一期 row6=入炉 row4=入厂 row5=餐厨；二期 row5=入炉 row4=入厂（含固废，标签为「二期」）
    in_row = 6 if phase == "p1" else 5
    plant_in_row = 4
    q_row = 25
    starts = P1_FURNACE_STARTS if phase == "p1" else P2_FURNACE_STARTS
    rows = []
    for col, d in days:
        try:
            pd.Timestamp(year=year, month=month, day=d)
        except ValueError:
            continue
        steam_t, mass_t = 0.0, 0.0
        n_on = 0
        for st in starts:
            burn = _num(ws.cell(st + 2, col).value) or 0.0
            steam = _num(ws.cell(st + 4, col).value) or 0.0
            if steam > 0 or burn > 0:
                n_on += 1
            steam_t += steam
            mass_t += burn
        plant_in = _num(ws.cell(plant_in_row, col).value)
        furnace_in = _num(ws.cell(in_row, col).value)
        kitchen = _num(ws.cell(5, col).value) if phase == "p1" else 0.0
        mass = mass_t if mass_t > 0 else (furnace_in or 0.0)
        if steam_t <= 0:
            ratio = _num(ws.cell(22, col).value)
            if ratio and mass:
                steam_t = ratio * mass
        if steam_t <= 0 and mass <= 0:
            continue
        flow = steam_t / 24.0
        typ = TYP[phase]
        rows.append({
            "time": f"{year}-{month:02d}-{d:02d}",
            "steam_flow": flow,
            "steam_p": typ["steam_p"],
            "steam_t": typ["steam_t"],
            "feedwater_t": typ["feedwater_t"],
            "o2": typ["o2"],
            "fluegas_t": typ["fluegas_t"],
            "phase_input": mass,
            "plant_in": plant_in,
            "kitchen": kitchen,
            "plant_q": _num(ws.cell(q_row, col).value),
            "steam_t_d": steam_t,
            "n_on": n_on,
        })
    return pd.DataFrame(rows)


def collect_year(year: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    p1s, p2s = [], []
    files = sorted(RAW.glob(f"dailyReport01_{year}-*.xlsx"))
    if not files:
        raise FileNotFoundError(f"no dailyReport01_{year}-*.xlsx in {RAW}")
    for p in files:
        y, m = map(int, p.stem.split("_")[1].split("-"))
        wb = load_workbook(p, data_only=True)
        names = wb.sheetnames
        p1s.append(parse_phase_sheet(wb[names[0]], y, m, "p1"))
        p2s.append(parse_phase_sheet(wb[names[1]], y, m, "p2"))
        print(p.name, "p1", len(p1s[-1]), "p2", len(p2s[-1]))
    p1 = pd.concat(p1s, ignore_index=True)
    p2 = pd.concat(p2s, ignore_index=True)
    p1["time"] = pd.to_datetime(p1["time"])
    p2["time"] = pd.to_datetime(p2["time"])
    return (
        p1.sort_values("time").drop_duplicates("time"),
        p2.sort_values("time").drop_duplicates("time"),
    )


def make_ledger(p1: pd.DataFrame, p2: pd.DataFrame, isw: pd.Series) -> pd.DataFrame:
    led = pd.DataFrame({"date": p1["time"].dt.normalize()})
    led["phase1_input"] = p1["phase_input"].values
    p2c = p2[["time", "phase_input", "plant_in", "plant_q"]].rename(columns={
        "time": "date", "phase_input": "phase2_input", "plant_in": "p2_in",
        "plant_q": "plant_q_p2",
    })
    led = led.merge(p2c, on="date", how="outer")
    p1c = p1[["time", "plant_in", "kitchen", "plant_q"]].rename(columns={
        "time": "date", "plant_in": "p1_in", "plant_q": "plant_q_p1",
    })
    led = led.merge(p1c, on="date", how="left")
    led["date"] = pd.to_datetime(led["date"]).dt.normalize()
    led["kitchen"] = led["kitchen"].fillna(0.0)
    fallback = (led["p1_in"].fillna(0) + led["p2_in"].fillna(0) - led["date"].map(isw).fillna(0)).clip(lower=0)
    cargo = load_cargo()
    if cargo is not None:
        led = led.merge(cargo, on="date", how="left")
        for c in ("isw_其他", "isw_大件", "isw_秸秆", "isw_固渣", "msw_wb", "msw_大件"):
            if c in led.columns:
                led[c] = led[c].fillna(0.0)
        # 固废 = dashboard 过滤口径（vehicle_entrance），不用地磅「其他+大件」合计
        led["isw"] = led["date"].map(isw).fillna(0.0)
        msw_base = led["msw_wb"] if "msw_wb" in led.columns else 0.0
        if "msw_大件" in led.columns:
            dajian = led["msw_大件"]
        elif "isw_大件" in led.columns:
            dajian = led["isw_大件"]
        else:
            dajian = 0.0
        msw = msw_base + dajian
        led["msw_in"] = msw.where(msw > 0, fallback)
    else:
        led["isw"] = led["date"].map(isw).fillna(0.0)
        led["msw_in"] = fallback
    for c in ("isw_其他", "isw_大件", "isw_秸秆", "isw_固渣"):
        if c not in led.columns:
            led[c] = 0.0
    cols = ["date", "phase1_input", "phase2_input", "msw_in", "isw",
            "isw_其他", "isw_大件", "isw_秸秆", "isw_固渣",
            "p1_in", "p2_in", "kitchen", "plant_q_p1", "plant_q_p2"]
    return led[[c for c in cols if c in led.columns]].copy()


def load_cargo() -> pd.DataFrame | None:
    p = OUT / "cargo_daily.csv"
    if not p.exists():
        return None
    df = pd.read_csv(p, parse_dates=["date"])
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    keep = ["date", "isw_其他", "isw_大件", "isw_秸秆", "isw_固渣", "msw_wb", "msw_大件"]
    return df[[c for c in keep if c in df.columns]]


def load_isw() -> pd.Series:
    if not DB.exists():
        return pd.Series(dtype=float)
    from hhv import db as _db  # noqa: E402
    con = _db.connect(DB)
    df = pd.read_sql_query(
        "SELECT entrance_date, COALESCE(net_weight, weight, 0) AS w FROM vehicle_entrance",
        con,
    )
    con.close()
    df["entrance_date"] = pd.to_datetime(df["entrance_date"], errors="coerce")
    return df.dropna(subset=["entrance_date"]).groupby(
        df["entrance_date"].dt.normalize()
    )["w"].sum()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    cols = ["time", "steam_flow", "steam_p", "steam_t", "feedwater_t", "o2", "fluegas_t"]

    print("== 2026 current ==")
    p1, p2 = collect_year(2026)
    led = make_ledger(p1, p2, load_isw())
    p1[cols].to_csv(OUT / "phase1_daily.csv", index=False, encoding="utf-8-sig")
    p2[cols].to_csv(OUT / "phase2_daily.csv", index=False, encoding="utf-8-sig")
    led.to_csv(OUT / "ledger_daily.csv", index=False, encoding="utf-8-sig")
    print("p1 days", len(p1), "p2", len(p2), "ledger", len(led),
          "isw median", led["isw"].median(), "msw median", led["msw_in"].median(),
          "kitchen/p1", round(float(led["kitchen"].sum() / led["p1_in"].sum()), 3))

    hist = sorted(RAW.glob("dailyReport01_2024-*.xlsx"))
    if hist:
        print("== 2024 hist (pure-burn; isw=0) ==")
        _h1, h2 = collect_year(2024)
        hled = make_ledger(_h1, h2, pd.Series(dtype=float))
        h2[cols].to_csv(OUT / "phase2_hist_daily.csv", index=False, encoding="utf-8-sig")
        hled.to_csv(OUT / "ledger_hist_daily.csv", index=False, encoding="utf-8-sig")
        print("hist p2 days", len(h2), "ledger", len(hled),
              "isw", hled["isw"].sum(), "msw median", hled["msw_in"].median())
    else:
        print("no 2024 files, skip hist")

    overlay_meeting(OUT / "phase1_daily.csv", OUT / "meeting_p1_daily.csv")
    overlay_meeting(OUT / "phase2_daily.csv", OUT / "meeting_p2_daily.csv")
    overlay_meeting(OUT / "phase2_hist_daily.csv", OUT / "meeting_p2_hist_daily.csv")

    print("wrote", OUT)
    return 0


def overlay_meeting(phase_csv: Path, meeting_csv: Path) -> None:
    if not phase_csv.exists() or not meeting_csv.exists():
        print("skip overlay", phase_csv.name, meeting_csv.name)
        return
    ph = pd.read_csv(phase_csv, parse_dates=["time"])
    mt = pd.read_csv(meeting_csv, parse_dates=["time"])
    keys = ["steam_p", "steam_t", "feedwater_t", "o2", "fluegas_t"]
    ph = ph.merge(mt[["time"] + keys], on="time", how="left", suffixes=("", "_m"))
    n = 0
    for k in keys:
        mk = k + "_m"
        if mk not in ph.columns:
            continue
        hit = ph[mk].notna()
        n = int(hit.sum())
        ph.loc[hit, k] = ph.loc[hit, mk]
        ph.drop(columns=[mk], inplace=True)
    ph.to_csv(phase_csv, index=False, encoding="utf-8-sig")
    print(f"overlay {meeting_csv.name} -> {phase_csv.name} {n}/{len(ph)}")


if __name__ == "__main__":
    raise SystemExit(main())
