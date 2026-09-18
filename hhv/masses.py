"""物料平衡层：台账滞后对齐 → 周尺度燃料量 → 掺烧比例 α。

滞后语义：进厂 →（垃圾池滞留 lag 天）→ 入炉，因此入炉量(t) = 进厂量(t - lag)。
未分来源时：用「入炉合计 − 滞后生活垃圾进厂」在入炉日估算综合固废（与 lag 口径一致）。

2026-09-02 库存变动改造：
- lag_scan：扫描 lag_days_msw ∈ [0, lag_search_max]，取周闭合残差 |balance_rel| 均值最小的 lag
- stock_rolling：入厂→入炉的库存缓冲用 4 周滚动累计差吸收（垃圾池容量有限，
  长周期进出必须配平；残差只剩周际抖动，回归 α 的噪声随之下降）
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .screening import iso_week


def _weekly_balance_rel(led: pd.DataFrame, lag_msw: int, lag_isw: int) -> pd.Series:
    """给定 lag 试算周闭合相对残差（内部用，lag 扫描的评价函数）。"""
    msw_shift = led[["msw_in"]].shift(lag_msw, freq="D")
    isw_cols = [c for c in led.columns if c.startswith("isw::")]
    if not isw_cols:
        est = (led["phase1_input"] + led["phase2_input"] - msw_shift["msw_in"]).clip(lower=0)
        isw_w = est.groupby(iso_week(est.index)).sum(min_count=1)
    else:
        shifted = led[isw_cols].shift(lag_isw, freq="D")
        shifted["week"] = iso_week(shifted.index)
        isw_w = shifted.groupby("week").sum(min_count=1).sum(axis=1)
    furnace = led[["phase1_input", "phase2_input"]].copy()
    furnace["week"] = iso_week(furnace.index)
    fw = furnace.groupby("week").sum(min_count=1).sum(axis=1)
    msw_w = msw_shift.groupby(iso_week(msw_shift.index)).sum(min_count=1)["msw_in"]
    rel = (fw - msw_w - isw_w) / fw.replace(0, np.nan)
    return rel.dropna()


def scan_lag(ledger: pd.DataFrame, mcfg: dict) -> tuple[int, dict]:
    """lag 扫描：lag ∈ [0, lag_search_max]，取闭合 |rel| 均值最小者。"""
    lag_max = int(mcfg.get("lag_search_max", 0) or 0)
    lag_isw = mcfg.get("lag_days_isw", 0)
    if lag_max <= 0:
        return int(mcfg.get("lag_days_msw", 3)), {}
    best_lag, best_score, table = None, np.inf, {}
    for lag in range(0, lag_max + 1):
        rel = _weekly_balance_rel(ledger, lag, lag_isw)
        score = float(rel.abs().mean()) if len(rel) else np.inf
        table[lag] = round(score, 4)
        if score < best_score:
            best_score, best_lag = score, lag
    return best_lag, {"scan_table": table, "best_lag": best_lag, "best_score": round(best_score, 4)}


def weekly_masses(ledger: pd.DataFrame, mcfg: dict) -> pd.DataFrame:
    """返回周索引表：phase1_in / phase2_in / msw_in / isw::*（吨/周）+ 物料闭合残差。

    mcfg 新键：
      lag_search_max: >0 时启用 lag 扫描（覆盖 lag_days_msw 的手工值）
      stock_rolling_weeks: >0 时对入厂量做库存缓冲吸收（滚动累计差回填）
    """
    led = ledger.copy()
    lag_info = {}
    if int(mcfg.get("lag_search_max", 0) or 0) > 0:
        lag_msw, lag_info = scan_lag(led, mcfg)
    else:
        lag_msw = mcfg.get("lag_days_msw", 3)
    lag_isw = mcfg.get("lag_days_isw", 0)
    msw_shift = led[["msw_in"]].shift(lag_msw, freq="D")
    isw_cols = [c for c in led.columns if c.startswith("isw::")]
    rw = int(mcfg.get("stock_rolling_weeks", 0) or 0)
    if not isw_cols:
        furnace_daily = led["phase1_input"].fillna(0) + led["phase2_input"].fillna(0)
        # 库存缓冲吸收（日级）：进厂-入炉的累积差 = 池位估计；快分量=磅表抖动，
        # 慢分量=真实池存变化。isw 估计只透传慢分量，快分量从入炉侧扣除。
        if rw > 1 and "p1_in" in led.columns and "p2_in" in led.columns:
            inbound_daily = led["p1_in"].fillna(0) + led["p2_in"].fillna(0)
            drift = (inbound_daily - furnace_daily).cumsum()
            window_days = rw * 7
            slow = drift.rolling(window_days, min_periods=7).mean().fillna(0)
            fast = drift - slow
            furnace_adj = furnace_daily + fast.diff().fillna(0)
        else:
            furnace_adj = furnace_daily
        est = (furnace_adj - msw_shift["msw_in"]).clip(lower=0)
        led["isw::工业固废综合"] = est
        isw_cols = ["isw::工业固废综合"]
        isw_shift = led[isw_cols]
    else:
        isw_shift = led[isw_cols].shift(lag_isw, freq="D")
    furnace = led[["phase1_input", "phase2_input"]]

    def weekly(df: pd.DataFrame) -> pd.DataFrame:
        w = df.copy()
        w["week"] = iso_week(w.index)
        return w.groupby("week").sum(min_count=1)

    out = weekly(furnace)
    out["msw_in"] = weekly(msw_shift)["msw_in"]
    for c in isw_cols:
        out[c] = weekly(isw_shift)[c]
    isw_sum = out[isw_cols].sum(axis=1)
    furnace_sum = out["phase1_input"] + out["phase2_input"]
    out["balance_residual"] = furnace_sum - out["msw_in"] - isw_sum
    out["balance_rel"] = out["balance_residual"] / furnace_sum.replace(0, np.nan)
    if "p1_in" in led.columns and "p2_in" in led.columns:
        inbound = pd.DataFrame({
            "inbound": led["p1_in"].fillna(0) + led["p2_in"].fillna(0),
        })
        in_shift = inbound.shift(lag_msw, freq="D")
        out["inbound"] = weekly(in_shift)["inbound"]
        out["stock_residual"] = furnace_sum - out["inbound"]
        out["stock_rel"] = out["stock_residual"] / furnace_sum.replace(0, np.nan)
        # 库存缓冲吸收：垃圾池容量有限，长周期进出配平。
        # 周际差分累积 = 池内存量变化；用滚动窗口把池位变化回填进入厂量，
        # 残差只剩周际抖动（回归 α 的噪声源从"库存全波动"降为"短期抖动"）。
        rw = int(mcfg.get("stock_rolling_weeks", 0) or 0)
        if rw > 1 and len(out) >= rw:
            absorbed = out["inbound"].copy()
            drift = out["inbound"].cumsum() - furnace_sum.cumsum()
            roll_mean = drift.rolling(rw, min_periods=2).mean().fillna(0)
            absorbed = out["inbound"] - (drift - roll_mean)
            out["inbound_stockadj"] = absorbed.clip(lower=0)
            out["stock_adj_residual"] = furnace_sum - out["inbound_stockadj"]
            out["stock_adj_rel"] = out["stock_adj_residual"] / furnace_sum.replace(0, np.nan)
    if "kitchen" in led.columns:
        out["kitchen"] = weekly(led[["kitchen"]].fillna(0))["kitchen"]
        if "p1_in" in led.columns:
            p1w = weekly(led[["p1_in"]].fillna(0))["p1_in"]
            out["kitchen_frac_p1"] = out["kitchen"] / p1w.replace(0, np.nan)
    out.attrs["isw_cols"] = isw_cols
    out.attrs["lag_info"] = lag_info
    return out


def weekly_alpha(wmass: pd.DataFrame, mcfg: dict | None = None) -> pd.DataFrame:
    """各来源 α_j 与总 α（周）。

    alpha_smooth_weeks（可选）：垃圾池是混料池——某周入炉成分 ≈ 进厂成分的
    N 周滚动平均（池内滞留把单周进厂波动抹平）。对 α_j 做同样的平滑，
    消除"单周进厂快照 vs 池内混料"的错位噪声；qv 不平滑（实测热/吨）。
    """
    out = wmass.copy()
    isw_cols = wmass.attrs["isw_cols"]
    sw = int((mcfg or {}).get("alpha_smooth_weeks", 0) or 0)
    for c in isw_cols:
        name = "alpha::" + c.split("::", 1)[1]
        out[name] = out[c] / out["phase2_input"].replace(0, np.nan)
        if sw > 1:
            out[name] = out[name].rolling(sw, min_periods=2).mean()
    out["alpha_total"] = sum(out["alpha::" + c.split("::", 1)[1]] for c in isw_cols)
    return out


def mass_on_valid_hours(valid_days: pd.DataFrame, phase_input_daily: pd.Series) -> pd.Series:
    """有效日入炉量按有效小时占比切分，与热量积分口径对齐。"""
    mass = phase_input_daily.copy()
    mass.index = pd.DatetimeIndex(mass.index).normalize()
    mass = mass.reindex(valid_days.index)
    th = valid_days["total_hours"].replace(0, np.nan)
    frac = (valid_days["valid_hours"] / th).clip(0, 1).fillna(1.0)
    return (mass * frac).rename("mass_scaled")


def weekly_heat_value(day_heat: pd.DataFrame, phase_input_daily: pd.Series,
                      prefix: str) -> pd.DataFrame:
    """某一期的周热值：有效小时热量合计 / 按小时占比切开的入炉量。"""
    valid = day_heat[day_heat["day_valid"] & day_heat["week_valid"]].copy()
    heat_w = valid.groupby("week")["_heat_in"].sum(min_count=1)
    mass = mass_on_valid_hours(valid, phase_input_daily)
    mass_w = mass.groupby(iso_week(mass.index)).sum(min_count=1)
    df = pd.DataFrame({f"heat_in_{prefix}": heat_w, f"mass_{prefix}": mass_w * 1000.0})
    df[f"qv_{prefix}"] = df[f"heat_in_{prefix}"] / df[f"mass_{prefix}"]
    return df
