"""时序/台账数据加载：列名映射、单位归一、数值清洗。"""
from __future__ import annotations

import pathlib

import numpy as np
import pandas as pd

STD_COLS = ["steam_flow", "steam_p", "steam_t", "feedwater_t",
            "o2", "fluegas_t", "spray_flow", "blowdown_flow"]


def _read_table(p: pathlib.Path, sheet=None) -> pd.DataFrame:
    if p.suffix.lower() in (".xlsx", ".xls"):
        return pd.read_excel(p, sheet_name=sheet if sheet else 0)
    last_err = None
    for enc in ("utf-8", "utf-8-sig", "gbk"):
        try:
            return pd.read_csv(p, encoding=enc)
        except UnicodeDecodeError as e:
            last_err = e
            continue
    raise ValueError(f"无法识别文件编码: {p} ({last_err})")


def _hours_per_row(index: pd.DatetimeIndex) -> float:
    if len(index) < 3:
        return 1.0
    dt = pd.Series(index).diff().median()
    if pd.isna(dt):
        return 1.0
    sec = float(dt.total_seconds())
    if sec <= 0:
        return 1.0
    if sec < 7200:
        return 1.0
    return float(min(24.0, sec / 3600.0))


def _resample_hourly(out: pd.DataFrame) -> pd.DataFrame:
    """亚小时数据重采样到小时均值；已是小时/日级则原样。"""
    out = out[~out.index.duplicated(keep="last")].sort_index()
    if len(out) < 3:
        return out
    dt = pd.Series(out.index).diff().median()
    if pd.isna(dt):
        return out
    sec = float(dt.total_seconds())
    if 0 < sec < 1800:
        out = out.resample("1h").mean()
    return out


def _finalize_timeseries(out: pd.DataFrame, units: dict,
                         missing_optional: list | None = None,
                         source: str = "", hours_per_row=None) -> pd.DataFrame:
    """单位归一、物理范围清洗、小时重采样。"""
    if units.get("steam_p", "MPa_g").lower() in ("mpa_g", "gauge", "表压"):
        out["steam_p"] = out["steam_p"] + 0.101325
    out.loc[(out["steam_flow"] < 0) | (out["steam_flow"] > 500), "steam_flow"] = np.nan
    out.loc[(out["steam_p"] < 0.5) | (out["steam_p"] > 15), "steam_p"] = np.nan
    if "steam_t" in out:
        out.loc[(out["steam_t"] < 150) | (out["steam_t"] > 600), "steam_t"] = np.nan
    out.loc[(out["feedwater_t"] < 20) | (out["feedwater_t"] > 250), "feedwater_t"] = np.nan
    if "o2" in out:
        out.loc[(out["o2"] < 0) | (out["o2"] > 21), "o2"] = np.nan
    if "fluegas_t" in out:
        out.loc[(out["fluegas_t"] < 60) | (out["fluegas_t"] > 400), "fluegas_t"] = np.nan
    out = _resample_hourly(out)
    out.attrs["missing_optional"] = missing_optional or []
    out.attrs["source"] = source
    out.attrs["hours_per_row"] = (
        _hours_per_row(out.index) if hours_per_row is None else hours_per_row)
    return out


def load_timeseries(path: str | pathlib.Path, colmap: dict, units: dict,
                    sheet=None) -> pd.DataFrame:
    """读取一台炉的时序文件，返回按标准列名、时间索引的 DataFrame。"""
    p = pathlib.Path(path)
    df = _read_table(p, sheet)
    tcol = colmap.get("time") or df.columns[0]
    df[tcol] = pd.to_datetime(df[tcol], errors="coerce")
    df = df.dropna(subset=[tcol]).sort_values(tcol)
    out = pd.DataFrame(index=df[tcol].rename("time"))
    missing_required, missing_optional = [], []
    for std in STD_COLS:
        src = colmap.get(std)
        if src and src in df.columns:
            out[std] = pd.to_numeric(df[src], errors="coerce").values
        elif std in ("steam_flow", "steam_p", "steam_t", "feedwater_t"):
            missing_required.append(std)
        elif std in ("o2", "fluegas_t"):
            missing_optional.append(std)
    if missing_required:
        raise ValueError(f"{p.name}: 缺少必需列 {missing_required}（检查 columns 映射）")
    return _finalize_timeseries(out, units, missing_optional, str(p))


def load_dcs_phase(db: str | pathlib.Path, daily: pd.DataFrame, units: dict,
                   start, end, dcfg: dict | None = None) -> pd.DataFrame:
    """二期：DCS 小时库 + 日报（流量尺度 / 主汽温 / 排烟温）。"""
    from . import dcs
    # daily 已 finalize：只用 steam_flow / steam_t / fluegas_t，DCS 压力仍按表压再转绝压
    out = dcs.load_phase_hourly(db, daily, start, end, dcfg)
    miss = []
    for std in ("o2", "fluegas_t"):
        if std not in out or not out[std].notna().any():
            miss.append(std)
    for std in ("steam_flow", "steam_p", "steam_t", "feedwater_t"):
        if std not in out:
            raise ValueError(f"DCS 小时拼表缺少必需列 {std}")
    notes = list(out.attrs.get("dcs_notes") or [])
    meta = dict(out.attrs.get("dcs_meta") or {})
    out = _finalize_timeseries(out, units, miss, "dcs_hourly+daily_fallback",
                               hours_per_row=1.0)
    meta["mean_steam_flow"] = round(float(out["steam_flow"].mean()), 2)
    meta["mean_steam_p"] = round(float(out["steam_p"].mean()), 3)
    meta["mean_feedwater_t"] = round(float(out["feedwater_t"].mean()), 1)
    if "o2" in out and out["o2"].notna().any():
        meta["mean_o2"] = round(float(out["o2"].mean()), 2)
    notes = list(out.attrs.get("dcs_notes") or notes)
    out.attrs["dcs_notes"] = notes
    out.attrs["dcs_meta"] = meta
    out.attrs["daily_source"] = daily.attrs.get("source")
    return out


def load_ledger(path: str | pathlib.Path, lmap: dict, sheet=None) -> pd.DataFrame:
    """读取台账，返回按日索引：phase1_input/phase2_input/msw_in + 各 ISW 来源。

    未配置分来源时不在此处用未滞后进厂量估固废——改由 masses 在滞后对齐后估算。
    """
    p = pathlib.Path(path)
    df = _read_table(p, sheet)
    dates = pd.to_datetime(df[lmap["date"]], errors="coerce")
    ok = dates.notna()
    df = df.loc[ok.to_numpy()].copy()
    dates = dates[ok]
    out = pd.DataFrame(index=dates.dt.normalize())
    out.index.name = "date"
    for std in ("phase1_input", "phase2_input", "msw_in"):
        src = lmap.get(std)
        if not src or src not in df.columns:
            raise ValueError(f"台账缺少必需列 {std}（检查 ledger_columns）")
        out[std] = pd.to_numeric(df[src], errors="coerce").to_numpy()
    for source, src in (lmap.get("isw_sources") or {}).items():
        if src and src in df.columns:
            out[f"isw::{source}"] = pd.to_numeric(df[src], errors="coerce").to_numpy()
        elif src is None:
            continue
        else:
            raise ValueError(f"台账缺少工业固废来源列: {src}")
    for opt in ("p1_in", "p2_in", "kitchen", "plant_q_p1", "plant_q_p2"):
        src = lmap.get(opt, opt)
        if src and src in df.columns:
            out[opt] = pd.to_numeric(df[src], errors="coerce").to_numpy()
    out = out.sort_index()
    out.attrs["source"] = str(p)
    return out
