"""二期 DCS 小时库 → 主链时序。

数据：data/snmis_history.db / hourly_hourly（f4/f5/f6）。
接法：
  主汽压力 / 给水温度 / 省煤器氧 — 运行炉算术均（炉膛温度或压力判开停）；
  主汽流量 — 日报产汽日均作总量，f4 小时曲线作形状
    （f5/f6 主汽流量拉取失败，不编造绝对流量）；
  主汽温度 / 排烟温度 — DCS 未拉，沿用日报列。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

POINT_STD = {
    "主汽流量": "steam_flow",
    "主汽压力": "steam_p",
    "给水温度": "feedwater_t",
    "省煤器氧": "o2",
    "炉膛温度": "furnace_t",
}

DEFAULTS = {
    "furnaces": ["f4", "f5", "f6"],
    "flow_shape_furnace": "f4",
    "on_furnace_t": 700.0,
    "on_steam_p": 2.0,
    "shape_min_flow": 5.0,
}


def _cfg(dcfg: dict | None) -> dict:
    out = dict(DEFAULTS)
    if dcfg:
        out.update({k: v for k, v in dcfg.items() if v is not None})
    return out


def load_hourly_long(db: str | Path, start: str, end: str,
                     furnaces: list[str]) -> pd.DataFrame:
    """hourly_hourly → 长表（time, furnace, point, mean）。"""
    from . import db as _db
    conn = _db.connect(db)
    ph = ",".join("?" * len(furnaces))
    df = pd.read_sql_query(
        f"SELECT day, hour, furnace, point, mean FROM hourly_hourly "
        f"WHERE day>=? AND day<=? AND furnace IN ({ph}) AND mean IS NOT NULL",
        conn,
        params=[str(start)[:10], str(end)[:10], *furnaces],
    )
    conn.close()
    if df.empty:
        raise ValueError(f"DCS 小时库无数据：{db} {start}~{end} {furnaces}")
    df["time"] = pd.to_datetime(df["day"]) + pd.to_timedelta(
        pd.to_numeric(df["hour"], errors="coerce").fillna(0).astype(int),
        unit="h",
    )
    return df[["time", "furnace", "point", "mean"]]


def _level(wide: pd.DataFrame, key: str) -> pd.DataFrame:
    if key in wide:
        return wide[key]
    return pd.DataFrame(index=wide.index)


def _furnace_on(wide: pd.DataFrame, on_t: float, on_p: float) -> pd.DataFrame:
    """每炉每小时是否在烧。缺测点时该条件视为 False。"""
    t = _level(wide, "furnace_t")
    p = _level(wide, "steam_p")
    cols = sorted(set(t.columns) | set(p.columns))
    t = t.reindex(columns=cols)
    p = p.reindex(columns=cols)
    return (t >= on_t) | (p >= on_p)


def _on_mean(wide: pd.DataFrame, key: str, on: pd.DataFrame) -> pd.Series:
    if key not in wide:
        return pd.Series(np.nan, index=wide.index, name=key)
    x = wide[key].reindex(columns=on.columns)
    masked = x.where(on.reindex(columns=x.columns, fill_value=False))
    return masked.mean(axis=1).rename(key)


def assemble_phase2(long: pd.DataFrame, daily: pd.DataFrame,
                    dcfg: dict | None = None) -> pd.DataFrame:
    """长表 + 日报时序 → 二期小时标准列。daily 需含 steam_flow，建议有 steam_t/fluegas_t。"""
    cfg = _cfg(dcfg)
    long = long.copy()
    long["std"] = long["point"].map(POINT_STD)
    long = long.dropna(subset=["std"])
    wide = long.pivot_table(
        index="time", columns=["std", "furnace"], values="mean", aggfunc="mean")
    on = _furnace_on(wide, float(cfg["on_furnace_t"]), float(cfg["on_steam_p"]))

    out = pd.DataFrame(index=wide.index)
    out.index.name = "time"
    out["steam_p"] = _on_mean(wide, "steam_p", on)
    out["feedwater_t"] = _on_mean(wide, "feedwater_t", on)
    out["o2"] = _on_mean(wide, "o2", on)

    shape_f = cfg["flow_shape_furnace"]
    f4 = wide["steam_flow"][shape_f] if (
        "steam_flow" in wide and shape_f in wide["steam_flow"].columns
    ) else pd.Series(np.nan, index=wide.index)
    day = out.index.normalize()
    min_flow = float(cfg["shape_min_flow"])
    f4_ok = f4.where(f4 >= min_flow)
    f4_mu = f4_ok.groupby(day).transform("mean")
    shape = (f4_ok / f4_mu).where(f4_mu > 0)

    daily = daily.copy()
    daily.index = pd.DatetimeIndex(daily.index).normalize()
    daily = daily[~daily.index.duplicated(keep="last")]
    daily_flow = daily["steam_flow"].reindex(day).to_numpy()
    out["steam_flow"] = daily_flow * shape.fillna(1.0).to_numpy()
    if "steam_t" in daily.columns:
        out["steam_t"] = daily["steam_t"].reindex(day).to_numpy()
    if "fluegas_t" in daily.columns:
        out["fluegas_t"] = daily["fluegas_t"].reindex(day).to_numpy()

    notes = [
        "steam_p/feedwater_t/o2=DCS运行炉均",
        f"steam_flow=日报日均×{shape_f}形状（f5/f6流量未入库）",
        "steam_t/fluegas_t=日报（DCS未拉）",
    ]
    n_on = on.sum(axis=1)
    out.attrs["source"] = "dcs_hourly+daily_fallback"
    out.attrs["hours_per_row"] = 1.0
    out.attrs["dcs_notes"] = notes
    out.attrs["dcs_meta"] = {
        "n_hours": int(len(out)),
        "window": [str(out.index.min()), str(out.index.max())],
        "frac_shaped": round(float(shape.notna().mean()), 3),
        "mean_furnaces_on": round(float(n_on.mean()), 2),
        "flow_shape_furnace": shape_f,
        "notes": notes,
    }
    return out


def load_phase_hourly(db: str | Path, daily: pd.DataFrame,
                      start, end, dcfg: dict | None = None) -> pd.DataFrame:
    cfg = _cfg(dcfg)
    start_s = pd.Timestamp(start).strftime("%Y-%m-%d")
    end_s = pd.Timestamp(end).strftime("%Y-%m-%d")
    long = load_hourly_long(db, start_s, end_s, list(cfg["furnaces"]))
    return assemble_phase2(long, daily, cfg)
