"""热平衡层：蒸汽侧正平衡 + 反平衡效率修正。

正平衡：Q_入炉 = [D×(h主汽-h给水) + D排污×(h排污-h给水)] / η
主蒸汽流量按过热器出口（已含减温水）。减温水取自给水时不再另加项。
日吸热量 = 有效时段均值负荷 × 有效小时（不再一律 ×24）。
反平衡：η = 1 - (q2+q3+q4+q5+q6)，逐日计算，两期各自取值。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import steam


def steam_enthalpy_series(p: pd.Series, t: pd.Series) -> pd.Series:
    """过热蒸汽焓（kJ/kg），逐点计算。"""
    return pd.Series([steam.h_steam(pi, ti) for pi, ti in zip(p, t)], index=p.index)


def heat_absorbed(day: pd.DataFrame) -> pd.Series:
    """蒸汽侧日吸热量 kJ/日（有效小时均值负荷 × 有效小时 × 1000 kg/t）。"""
    h_ms = steam_enthalpy_series(day["steam_p"], day["steam_t"])
    h_fw = day["feedwater_t"].map(steam.h_feedwater)
    q = day["steam_flow"] * (h_ms - h_fw)  # t/h × kJ/kg = 10^3 kJ/h
    if "blowdown_flow" in day and day["blowdown_flow"].notna().any():
        h_bd = day["steam_p"].map(steam.h_satliquid)
        q = q + day["blowdown_flow"].fillna(0.0) * (h_bd - h_fw)
    hours = day["valid_hours"] if "valid_hours" in day.columns else 24.0
    hours = pd.to_numeric(hours, errors="coerce").fillna(0.0).clip(lower=0)
    return q * hours * 1000.0


def reverse_efficiency(day: pd.DataFrame, ecfg: dict, rated_flow: float):
    """反平衡效率（逐日）。缺 O2/排烟温度时退化为典型常数并告警。"""
    Ta = ecfg.get("ambient_t", 20.0)
    q3 = 0.1
    q5 = ecfg.get("q5_rated", 1.0) * rated_flow / day["steam_flow"].clip(lower=1e-6)
    q6 = ecfg.get("q6_default", 0.2)
    has_fg = "fluegas_t" in day and bool(day["fluegas_t"].notna().any())
    has_o2 = "o2" in day and bool(day["o2"].notna().any())
    if has_fg and has_o2:
        alpha_air = 21.0 / (21.0 - day["o2"].clip(0, 20.9))
        q2 = (ecfg.get("q2_k1", 3.55) * alpha_air + ecfg.get("q2_k2", 0.44)) \
            * (day["fluegas_t"] - Ta) / 100.0
    else:
        alpha_typ = 21.0 / (21.0 - 8.0)
        tfg_typ = 190.0
        q2 = pd.Series((ecfg.get("q2_k1", 3.55) * alpha_typ + ecfg.get("q2_k2", 0.44))
                       * (tfg_typ - Ta) / 100.0, index=day.index)
    q4 = pd.Series(ecfg.get("q4_default", 2.0), index=day.index)
    eta = 1.0 - (q2 + q3 + q4 + q5.clip(0, 4) + q6) / 100.0
    eta = eta.clip(0.60, 0.92)
    meta = {"q2_typ": float(np.nanmean(q2)), "has_o2": has_o2, "has_fg": has_fg,
            "eta_typ": float(np.nanmean(eta))}
    return eta, meta


def daily_heat_input(day: pd.DataFrame, ecfg: dict, rated_flow: float) -> pd.DataFrame:
    """日表附 _heat_in(kJ/日)、_eta、_q_abs、_h_ms、_h_fw。"""
    out = day.copy()
    q_abs = heat_absorbed(day)
    if ecfg.get("mode", "reverse_balance") == "constant":
        eta = pd.Series(ecfg.get("eta_constant", 0.82), index=day.index)
        meta = {"mode": "constant", "eta_typ": float(ecfg.get("eta_constant", 0.82))}
    else:
        eta, meta = reverse_efficiency(day, ecfg, rated_flow)
        meta["mode"] = "reverse_balance"
    out["_q_abs"] = q_abs
    out["_eta"] = eta
    out["_heat_in"] = q_abs / eta.clip(0.5, 0.99)
    out["_h_ms"] = steam_enthalpy_series(day["steam_p"], day["steam_t"])
    out["_h_fw"] = day["feedwater_t"].map(steam.h_feedwater)
    out.attrs["eff_meta"] = meta
    return out
