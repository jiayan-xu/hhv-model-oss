# -*- coding: utf-8 -*-
"""交叉验证闸门（k 因子）与同炉历史周对齐。

比较：二期两年前纯烧（同月份）Q_生活 vs 一期当期纯烧（同月份）。
k = median(Q_hist / Q_now)，用于把一期同周锚换到二期表计/效率刻度。
δ = |k-1| 等价于月份对齐相对差：
  δ < gate1(5%)  → PASS   一期同周×k 可作主锚
  gate1 ≤ δ < gate2(10%) → DUAL   一期同周×k 与同炉历史锚并报
  δ ≥ gate2      → ALERT  禁止单锚定价
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import masses


def monthly_mean_qv(day_heat: pd.DataFrame, phase_input_daily: pd.Series) -> pd.Series:
    """月尺度 Q_生活 均值（热量与质量都按有效小时占比）。"""
    valid = day_heat[day_heat["day_valid"] & day_heat["week_valid"]].copy()
    mass = masses.mass_on_valid_hours(valid, phase_input_daily)
    heat_m = valid["_heat_in"].groupby(valid.index.month).sum(min_count=1)
    mass_m = mass.groupby(mass.index.month).sum(min_count=1) * 1000.0
    return (heat_m / mass_m).rename("qv_month")


def k_factor(q_hist: pd.Series, q_now: pd.Series, drop_extreme: bool = True) -> float:
    """月份对齐的刻度比 k = median(Q_hist / Q_now)。无重叠则 1。

    drop_extreme：重叠月 ≥5 时，丢掉 |k_m − median|/median > 8% 的月再取中位
    （抑制 8 月这类单月结构突变把全局 k 拽飞）。
    """
    months = sorted(set(q_hist.index) & set(q_now.index))
    if not months:
        return 1.0
    r = q_hist.loc[months] / q_now.loc[months].replace(0, np.nan)
    arr = r.to_numpy(dtype=float)
    k = float(np.nanmedian(arr))
    if drop_extreme and len(months) >= 5 and np.isfinite(k) and k > 0:
        keep = np.abs(arr - k) / k <= 0.08
        if keep.sum() >= 3:
            k = float(np.nanmedian(arr[keep]))
    if not np.isfinite(k) or k <= 0:
        return 1.0
    return k


def iso_week_num(label) -> str:
    s = str(label)
    if "-W" in s:
        return s.split("-W")[-1]
    return s


def align_hist_weekly(
    current_weeks,
    hist_qv: pd.Series,
    current_kitchen: pd.Series | None = None,
    hist_kitchen: pd.Series | None = None,
) -> pd.Series:
    """按 ISO 周序号把历史周 Q 对齐到当期周；缺周用历史中位数填。

    若提供当期/历史餐厨占比（0–1），同 ISO 周内的多个历史年用
    exp(−|Δkitchen|/0.15) 加权，避免「餐厨 60% 的当期周」硬套「餐厨 20% 的历史周」。
    """
    by_w: dict[str, list] = {}
    for idx, val in hist_qv.items():
        if pd.isna(val):
            continue
        kit_h = None
        if hist_kitchen is not None:
            try:
                kit_h = float(hist_kitchen.loc[idx]) if idx in hist_kitchen.index else None
            except Exception:
                kit_h = None
        by_w.setdefault(iso_week_num(idx), []).append((float(val), kit_h))
    fallback = float(np.nanmedian(hist_qv.to_numpy())) if len(hist_qv) else np.nan
    vals = []
    for w in current_weeks:
        bucket = by_w.get(iso_week_num(w))
        if not bucket:
            vals.append(fallback)
            continue
        if current_kitchen is None or hist_kitchen is None or len(bucket) == 1:
            vals.append(float(np.mean([v for v, _ in bucket])))
            continue
        try:
            kc = float(current_kitchen.loc[w])
        except Exception:
            kc = float("nan")
        if not np.isfinite(kc):
            vals.append(float(np.mean([v for v, _ in bucket])))
            continue
        ws, vs = [], []
        for v, kh in bucket:
            if kh is None or not np.isfinite(kh):
                wgt = 0.25  # 无餐厨信息的历史年降权
            else:
                wgt = float(np.exp(-abs(kc - kh) / 0.15))
            ws.append(wgt)
            vs.append(v)
        wsum = sum(ws) or 1.0
        vals.append(float(sum(w * v for w, v in zip(ws, vs)) / wsum))
    return pd.Series(vals, index=current_weeks, name="qv_hist_aligned")


def select_primary_report(gate: dict, gate_plant: dict | None, gate_eta: dict | None) -> tuple[str, str]:
    """闸门分层选口径（C）：谁更信得过，主报就跟谁。

    - 效率互证 WARN → 禁止单锚
    - 蒸汽 DUAL/ALERT 且厂内平行更优 → 主参考厂内 Q
    - 否则沿用原 PASS/B / DUAL / ALERT 规则
    """
    steam_st = (gate or {}).get("status") or "NO_HIST"
    plant_st = (gate_plant or {}).get("status")
    eta_st = (gate_eta or {}).get("status")
    steam_d = gate.get("delta_mean")
    plant_d = (gate_plant or {}).get("delta_mean")
    steam_d = float(steam_d) if steam_d is not None else 1.0
    plant_d = float(plant_d) if plant_d is not None else 1.0
    plant_better = (
        plant_st == "PASS"
        and steam_st in ("DUAL", "ALERT")
        and plant_d < steam_d * 0.85
    )
    if eta_st == "WARN":
        return "ALERT_ETA", "效率互证超限，禁止单锚定价。"
    if steam_st == "ALERT":
        if plant_better:
            return ("ALERT_PLANT",
                    f"蒸汽闸门 ALERT（δ={steam_d:.1%}）但厂内平行 PASS（δ={plant_d:.1%}）"
                    "——主参考厂内 Q，蒸汽锚仅作排查，禁止单锚。")
        return "ALERT", "蒸汽交叉验证 ALERT，禁止单锚定价。"
    if steam_st == "DUAL":
        if plant_better:
            return ("DUAL_PLANT",
                    f"双锚并报；厂内平行（δ={plant_d:.1%}）优于蒸汽（δ={steam_d:.1%}），"
                    "月度解释与对外口径优先厂内 Q。")
        return "DUAL", "蒸汽 DUAL：一期同周×k 与二期同炉历史锚并报。"
    if steam_st == "PASS":
        return "B", "蒸汽闸门 PASS：主锚=一期同周×k。"
    return steam_st, gate.get("msg") or ""


def k_gate(q_hist: pd.Series, q_now: pd.Series, ccfg: dict) -> dict:
    """月份对齐后做闸门判定，并给出 k。"""
    months = sorted(set(q_hist.index) & set(q_now.index))
    if len(months) < 1:
        return {"status": "NO_OVERLAP", "detail": None, "k": 1.0,
                "msg": "历史与当期无重叠月份，无法做交叉验证闸门；k=1，仅一期同周锚。"}
    h = q_hist.loc[months]
    n = q_now.loc[months]
    k = k_factor(h, n)
    delta = ((h - n).abs() / n.replace(0, np.nan))
    delta_mean = float(delta.mean())
    g1, g2 = ccfg.get("gate1", 0.05), ccfg.get("gate2", 0.10)
    if delta_mean < g1:
        status = "PASS"
        msg = (f"交叉验证通过：月份对齐差值均值 {delta_mean:.1%} < {g1:.0%}，"
               f"k={k:.4f}。主锚=一期同周×k。")
    elif delta_mean < g2:
        status = "DUAL"
        msg = (f"交叉验证差值 {delta_mean:.1%} 落在 {g1:.0%}~{g2:.0%}："
               f"k={k:.4f}。一期同周×k 与二期同炉历史锚并报。")
    else:
        status = "ALERT"
        msg = (f"交叉验证差值 {delta_mean:.1%} ≥ {g2:.0%}，k={k:.4f}："
               "禁止单锚定价；先查表计/锅炉改造/收运范围，两锚仅供排查。")
    detail = pd.DataFrame({"month": months, "qv_hist": h.values,
                           "qv_now": n.values, "delta": delta.values,
                           "k_month": (h / n.replace(0, np.nan)).values})
    return {"status": status, "delta_mean": delta_mean, "k": k,
            "detail": detail, "msg": msg}


def monthly_mean_plant_q(ledger: pd.DataFrame, col: str, min_days: int = 10) -> pd.Series | None:
    """厂内填报热值的月均值（不经反平衡）。不满 min_days 的月丢掉（避免 9/1 单日）。"""
    if col not in ledger.columns:
        return None
    s = pd.to_numeric(ledger[col], errors="coerce").dropna()
    if s.empty:
        return None
    g = s.groupby(s.index.month)
    mu = g.mean()
    ok = g.size()
    mu = mu[ok >= min_days]
    if mu.empty:
        return None
    return mu.rename(col)


def plant_q_gate(ledger: pd.DataFrame, ledger_hist: pd.DataFrame, ccfg: dict) -> dict | None:
    """用生产日报「垃圾热值」做平行闸门：历史二期 vs 当期一期。"""
    q_hist = monthly_mean_plant_q(ledger_hist, "plant_q_p2")
    q_now = monthly_mean_plant_q(ledger, "plant_q_p1")
    if q_hist is None or q_now is None:
        return None
    g = k_gate(q_hist, q_now, ccfg)
    g["kind"] = "plant_q"
    dm = g.get("delta_mean")
    dm_s = f"{dm:.1%}" if dm is not None else "n/a"
    g["msg"] = (f"【厂内热值】月份对齐差值均值 {dm_s}，k={g['k']:.4f}。"
                f"判定 {g['status']}（只对照、不进回归）。")
    return g


def monthly_kitchen_frac(ledger: pd.DataFrame) -> pd.Series | None:
    """一期餐厨 / 一期入厂，按月。"""
    if "kitchen" not in ledger.columns or "p1_in" not in ledger.columns:
        return None
    kit = ledger["kitchen"].fillna(0)
    pin = ledger["p1_in"].fillna(0)
    months = kit.index.month
    n = kit.groupby(months).size()
    g = kit.groupby(months).sum() / pin.groupby(months).sum().replace(0, np.nan)
    g = g[n >= 10]
    return g.rename("kitchen_frac")


def eta_implied_gate(steam_flow: pd.Series, dh: pd.Series, mass: pd.Series,
                     lhv_mj: pd.Series, eta_reverse: pd.Series,
                     tol: float = 0.05) -> dict:
    """DCS 隐含效率 vs 反平衡效率的互证闸门。"""
    idx = steam_flow.dropna().index
    ok = idx.intersection(dh.dropna().index).intersection(mass.dropna().index) \
            .intersection(lhv_mj.dropna().drop(lhv_mj[(lhv_mj <= 0) | (lhv_mj > 20)].index).index) \
            .intersection(eta_reverse.dropna().index)
    if len(ok) < 10:
        return {"status": "SKIP", "n_days": len(ok),
                "msg": f"DCS LHV 或运行数据不足（{len(ok)} 天 < 10），跳过效率互证闸门。"}
    eta_dcs = steam_flow[ok] * 24 * dh[ok] / (mass[ok] * 1000 * lhv_mj[ok])
    eta_dcs = eta_dcs.clip(0.5, 1.0)
    diff_rel = (eta_dcs - eta_reverse[ok]) / eta_reverse[ok]
    month = ok.month if hasattr(ok, "month") else pd.Series(ok).dt.month.values
    m_diff = diff_rel.groupby(month).mean()
    worst = m_diff.abs().max()
    if worst < tol:
        status = "PASS"
        msg = (f"效率互证通过：DCS 隐含 η 均值 {eta_dcs.mean():.3f}，反平衡 η 均值 "
               f"{eta_reverse[ok].mean():.3f}，月均偏差最大 {worst:.1%} < {tol:.0%}。"
               "三道闸门（k / 厂内平行 / 效率互证）全通。")
    else:
        bad_m = m_diff.abs().idxmax()
        status = "WARN"
        msg = (f"效率互证超限：{bad_m} 月 DCS 隐含 η 与反平衡 η 偏差 {m_diff[bad_m]:+.1%}。"
               "排查方向：DCS LHV 点口径 / 反平衡 q2~q6 参数 / 入炉量台账。")
    return {
        "status": status, "msg": msg, "n_days": len(ok),
        "eta_dcs_mean": float(eta_dcs.mean()), "eta_dcs_std": float(eta_dcs.std()),
        "eta_rb_mean": float(eta_reverse[ok].mean()),
        "eta_dcs_p05": float(eta_dcs.quantile(0.05)),
        "eta_dcs_p95": float(eta_dcs.quantile(0.95)),
        "monthly_diff": {int(k): float(v) for k, v in m_diff.items()},
    }


def eta_implied_gate_from_db(day_heat: pd.DataFrame, ledger: pd.DataFrame,
                             cfg: dict) -> dict:
    """从 DCS 小时库（snmis_history.db）拉 LHV 日均值，跑效率互证闸门。"""
    import sqlite3
    from pathlib import Path
    raw_db = cfg.get("paths", {}).get("dcs_hourly_db") or cfg.get("paths", {}).get("dcs_db")
    if not raw_db:
        return {"status": "SKIP", "n_days": 0,
                "msg": "config 未配置 paths.dcs_hourly_db，跳过效率互证闸门。"}
    db = Path(raw_db)
    if not db.exists():
        return {"status": "SKIP", "n_days": 0,
                "msg": f"DCS 小时库不存在（{db}），跳过效率互证闸门。"}
    try:
        from . import db as _db
        conn = _db.connect(db)
        lhv = pd.read_sql_query(
            "SELECT day, AVG(mean) AS lhv FROM hourly_hourly "
            "WHERE point='LHV' AND mean > 0 AND mean < 20 GROUP BY day",
            conn, parse_dates=["day"])
        conn.close()
    except Exception as e:
        return {"status": "SKIP", "n_days": 0, "msg": f"DCS 小时库读取失败：{e}"}
    if lhv.empty:
        return {"status": "SKIP", "n_days": 0, "msg": "hourly_hourly 无 LHV 数据，跳过效率互证闸门。"}

    valid = day_heat[day_heat["day_valid"] & day_heat["week_valid"]].copy()
    valid = valid.join(lhv.set_index("day")["lhv"], how="inner").dropna(
        subset=["steam_flow", "_eta", "lhv"])
    mass = ledger["phase2_input"].copy()
    mass.index = pd.to_datetime(mass.index)
    mass = mass[mass.index.isin(valid.index)]
    if len(valid) < 10:
        return {"status": "SKIP", "n_days": len(valid),
                "msg": f"有效日与 LHV 重叠不足（{len(valid)} 天 < 10），跳过效率互证闸门。"}
    dh = valid["_h_ms"] - valid["_h_fw"]
    return eta_implied_gate(valid["steam_flow"], dh, mass, valid["lhv"],
                            valid["_eta"], cfg.get("calibration", {}).get("eta_tol", 0.05))
