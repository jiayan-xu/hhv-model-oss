"""异常工况自动筛选：小时有效性 → 日级准入 → 周级准入。

规则（报告 2.4 节）：
- 有效小时：主汽流量 ≥ max(额定, 中位数) × min_load_frac，且温压测点有效；
- 阶跃事件（相邻小时流量变化 > step_jump）前后 step_guard_hours 小时剔除；
  日级数据不做小时护栏；
- 有效日：全天有效小时 ≥ min_valid_hours（日级行按 hours_per_row 折合）；
- 有效周：有效日 ≥ min_valid_days_per_week。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def screen_hours(df: pd.DataFrame, sc: dict, rated_flow: float | None = None) -> pd.DataFrame:
    """标记每小时是否有效，返回附 _valid 列的副本。"""
    d = df.copy()
    hpr = float(df.attrs.get("hours_per_row", 1.0) or 1.0)
    d.attrs["hours_per_row"] = hpr
    flow = d["steam_flow"]
    median_load = float(np.nanmedian(flow))
    if rated_flow and float(rated_flow) > 0:
        thresh = float(rated_flow) * sc["min_load_frac"]
    else:
        thresh = median_load * sc["min_load_frac"]
    ok_load = flow >= thresh
    ok_meter = d[["steam_p", "steam_t", "feedwater_t"]].notna().all(axis=1)
    valid = ok_load & ok_meter & flow.notna()
    rel = flow.pct_change(fill_method=None).abs()
    events = rel > sc["step_jump"]
    g = 0 if hpr >= 12 else int(sc["step_guard_hours"])
    guard = events.copy()
    for k in range(1, g + 1):
        guard |= events.shift(k, fill_value=False)
        guard |= events.shift(-k, fill_value=False)
    if g == 0:
        guard[:] = False
    d["_valid"] = valid & ~guard
    d.attrs["median_load"] = median_load
    d.attrs["load_thresh"] = thresh
    d.attrs["n_step_events"] = int(events.sum())
    return d


def aggregate_days(d: pd.DataFrame, sc: dict) -> pd.DataFrame:
    """小时 → 日：有效小时均值、有效小时数、日准入。"""
    d = d.copy()
    hpr = float(d.attrs.get("hours_per_row", 1.0) or 1.0)
    d["date"] = d.index.normalize()
    grp = d[d["_valid"]].groupby("date")
    day = grp.agg(
        steam_flow=("steam_flow", "mean"),
        steam_p=("steam_p", "mean"),
        steam_t=("steam_t", "mean"),
        feedwater_t=("feedwater_t", "mean"),
        o2=("o2", "mean") if "o2" in d else ("steam_flow", "size"),
        fluegas_t=("fluegas_t", "mean") if "fluegas_t" in d else ("steam_flow", "size"),
        spray_flow=("spray_flow", "mean") if "spray_flow" in d else ("steam_flow", "size"),
        blowdown_flow=("blowdown_flow", "mean") if "blowdown_flow" in d else ("steam_flow", "size"),
        valid_hours=("steam_flow", "size"),
    )
    for c in ("o2", "fluegas_t", "spray_flow", "blowdown_flow"):
        if c not in d:
            day[c] = np.nan
    total_hours = d.groupby("date")["steam_flow"].size()
    day["total_hours"] = total_hours
    full = pd.date_range(d["date"].min(), d["date"].max(), freq="D")
    day = day.reindex(full)
    day["valid_hours"] = day["valid_hours"].fillna(0) * hpr
    day["total_hours"] = day["total_hours"].fillna(0) * hpr
    day["day_valid"] = day["valid_hours"] >= sc["min_valid_hours"]
    excl = exclude_index(sc)
    if len(excl):
        day.loc[day.index.isin(excl), "day_valid"] = False
    day.attrs["hours_per_row"] = hpr
    return day


def exclude_index(sc: dict) -> pd.DatetimeIndex:
    """screening.exclude_dates：整日剔除。"""
    raw = sc.get("exclude_dates") or []
    if not raw:
        return pd.DatetimeIndex([])
    return pd.DatetimeIndex(pd.to_datetime(list(raw), errors="coerce")).dropna().normalize()


def iso_week(index: pd.DatetimeIndex) -> pd.Series:
    """ISO 年-周标签（跨年安全）。"""
    iso = index.isocalendar()
    return (iso["year"].astype(str) + "-W" + iso["week"].astype(str).str.zfill(2)).astype(str)


def weekly_admission(day: pd.DataFrame, sc: dict) -> pd.DataFrame:
    """日 → 周：周有效日数与准入。返回带 week 标签的日表。"""
    day = day.copy()
    day["week"] = iso_week(day.index)
    valid_days = day[day["day_valid"]].groupby("week")["day_valid"].size()
    week_ok = valid_days >= sc["min_valid_days_per_week"]
    day["week_valid"] = day["week"].map(lambda w: bool(week_ok.get(w, False)))
    return day


def screening_stats(day: pd.DataFrame, sc: dict | None = None) -> dict:
    min_d = (sc or {}).get("min_valid_days_per_week", 5)
    total = len(day)
    valid_d = int(day["day_valid"].sum())
    weeks = day.groupby("week")["day_valid"].sum()
    return {
        "total_days": total,
        "valid_days": valid_d,
        "drop_rate_pct": round(100 * (total - valid_d) / max(total, 1), 1),
        "total_weeks": int(len(weeks)),
        "valid_weeks": int((weeks >= min_d).sum()),
    }
