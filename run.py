"""主入口：python run.py --config config.demo.yaml"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from hhv import balance, calib, config as cfgmod, loader, masses, plots, regression, report, screening  # noqa: E402


def _weekly_kitchen_frac(led) -> pd.Series | None:
    """台账 → 周餐厨占比（一期餐厨 / 一期入厂），索引与 weekly_heat_value 一致。"""
    if led is None or "kitchen" not in getattr(led, "columns", []):
        return None
    if "p1_in" not in led.columns:
        return None
    from hhv import masses as _m
    kit = led[["kitchen"]].fillna(0).copy()
    pin = led[["p1_in"]].fillna(0).copy()
    kit["week"] = _m.iso_week(kit.index)
    pin["week"] = _m.iso_week(pin.index)
    kw = kit.groupby("week")["kitchen"].sum(min_count=1)
    pw = pin.groupby("week")["p1_in"].sum(min_count=1)
    frac = (kw / pw.replace(0, np.nan)).rename("kitchen_frac_p1")
    return frac if frac.notna().any() else None


def _pipeline_phase(ts, cfg, sc, rated_flow):
    d = screening.screen_hours(ts, sc, rated_flow=rated_flow)
    day = screening.aggregate_days(d, sc)
    day = screening.weekly_admission(day, sc)
    bh = balance.daily_heat_input(day, cfg["efficiency"], rated_flow)
    return bh


def _print_ci(tag, ci):
    print(f"{tag}：")
    for nm, d in ci.items():
        if not nm.startswith("_"):
            print(f"  {nm}: {d['mean']:.0f}  (CI {d['lo']:.0f} ~ {d['hi']:.0f})")


def _ci_json(ci):
    if not ci:
        return None
    out = {}
    for k, v in ci.items():
        if k.startswith("_") and k != "_n_boot":
            continue
        if isinstance(v, dict):
            out[k] = {kk: float(vv) for kk, vv in v.items()}
        else:
            out[k] = v
    return out


def _gate_json(g):
    if not g:
        return None
    detail = g.get("detail")
    rows = None
    if detail is not None and hasattr(detail, "to_dict"):
        rows = []
        for rec in detail.to_dict(orient="records"):
            rows.append({k: (float(v) if hasattr(v, "real") else v) for k, v in rec.items()})
    dm = g.get("delta_mean")
    return {
        "status": g.get("status"),
        "k": float(g.get("k") or 1.0),
        "delta_mean": None if dm is None else float(dm),
        "msg": g.get("msg"),
        "detail": rows,
    }


def _result_json(cfg, res):
    kit = res.get("kitchen_monthly") or {}
    kit = {str(k): float(v) for k, v in kit.items()}
    return {
        "project": cfg.get("project"),
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "primary_label": res.get("primary_label"),
        "primary_code": res.get("primary_code"),
        "primary_note": res.get("primary_note"),
        "n_weeks": res.get("n_weeks"),
        "anchor_k": res.get("anchor_k"),
        "balance_rel_mean": res.get("balance_rel_mean"),
        "stock_rel_mean": res.get("stock_rel_mean"),
        "stats1": res.get("stats1"),
        "stats2": res.get("stats2"),
        "eff1": {k: v if isinstance(v, (str, bool)) else float(v)
                 for k, v in (res.get("eff1") or {}).items()
                 if k in ("mode", "eta_typ", "has_o2", "has_fg")},
        "eff2": {k: v if isinstance(v, (str, bool)) else float(v)
                 for k, v in (res.get("eff2") or {}).items()
                 if k in ("mode", "eta_typ", "has_o2", "has_fg")},
        "gate": _gate_json(res.get("gate")),
        "gate_plant": _gate_json(res.get("gate_plant")),
        "gate_eta": _eta_json(res.get("gate_eta")),
        "ci_free": _ci_json(res.get("ci_free")),
        "ci_anchored": _ci_json(res.get("ci_anchored")),
        "ci_furnace": _ci_json(res.get("ci_furnace")),
        "kitchen_monthly": kit,
        "dcs2": res.get("dcs2"),
        "dcs_hist": res.get("dcs_hist"),
    }


def main(cfg_path: str) -> int:
    cfg = cfgmod.load_config(cfg_path)
    outdir = pathlib.Path(cfg["paths"]["output_dir"]) / pathlib.Path(cfg_path).stem
    (outdir / "figures").mkdir(parents=True, exist_ok=True)

    ts1 = loader.load_timeseries(cfg["paths"]["phase1"], cfg["columns"], cfg["units"], cfg.get("sheet"))
    ts2 = loader.load_timeseries(cfg["paths"]["phase2"], cfg["columns"], cfg["units"], cfg.get("sheet"))
    tsh = (loader.load_timeseries(cfg["paths"]["phase2_hist"], cfg["columns"], cfg["units"], cfg.get("sheet"))
           if cfg["paths"].get("phase2_hist") else None)
    ledger = loader.load_ledger(cfg["paths"]["ledger"], cfg["ledger_columns"], cfg.get("sheet"))
    ledger_hist = (loader.load_ledger(cfg["paths"]["ledger_hist"], cfg["ledger_columns"], cfg.get("sheet"))
                   if cfg["paths"].get("ledger_hist") else None)

    dcs_cfg = cfg.get("dcs") or {}
    if dcs_cfg.get("enabled"):
        db = cfg["paths"]["dcs_hourly_db"]
        ts2 = loader.load_dcs_phase(
            db, ts2, cfg["units"], ledger.index.min(), ledger.index.max(), dcs_cfg)
        print(f"二期时序改接 DCS 小时库：{ts2.attrs.get('dcs_meta')}")
        if tsh is not None and ledger_hist is not None:
            tsh = loader.load_dcs_phase(
                db, tsh, cfg["units"], ledger_hist.index.min(), ledger_hist.index.max(),
                dcs_cfg)
            print(f"二期历史时序改接 DCS 小时库：{tsh.attrs.get('dcs_meta')}")

    sc = cfg["screening"]
    excl = screening.exclude_index(sc)
    if len(excl):
        ledger = ledger[~ledger.index.isin(excl)].copy()
        print(f"剔除日期 {len(excl)} 天（蒸汽按无效日、台账不计入周合计）")
    bh1 = _pipeline_phase(ts1, cfg, sc, cfg["boilers"]["phase1"]["rated_flow"])
    bh2 = _pipeline_phase(ts2, cfg, sc, cfg["boilers"]["phase2"]["rated_flow"])
    bhh = (_pipeline_phase(tsh, cfg, sc, cfg["boilers"]["phase2"]["rated_flow"])
           if tsh is not None else None)
    stats1 = screening.screening_stats(bh1, sc)
    stats2 = screening.screening_stats(bh2, sc)

    wmass = masses.weekly_masses(ledger, cfg["masses"])
    walpha = masses.weekly_alpha(wmass, cfg["masses"])
    alpha_cols = [c for c in walpha.columns if c.startswith("alpha::")]
    extra = [c for c in ("alpha_total", "balance_residual", "balance_rel",
                         "stock_residual", "stock_rel", "kitchen", "kitchen_frac_p1",
                         "msw_in", "phase1_input", "phase2_input", "inbound") if c in walpha]
    wq1 = masses.weekly_heat_value(bh1, ledger["phase1_input"], "p1")
    wq2 = masses.weekly_heat_value(bh2, ledger["phase2_input"], "p2")
    weekly = wq1.join(wq2, how="inner").join(walpha[alpha_cols + extra], how="inner")
    weekly = weekly.dropna(subset=["qv_p1", "qv_p2", "alpha_total"])
    weekly = weekly[weekly["alpha_total"].between(0.005, 0.95)]
    if len(weekly) < 6:
        raise RuntimeError(f"合格周仅 {len(weekly)} 个（<6），无法回归——检查数据窗口/α 波动/筛选参数")
    qmix = weekly["qv_p2"]
    alphas = weekly[alpha_cols].clip(0, 0.95)

    if bhh is not None and ledger_hist is not None:
        q_hist = calib.monthly_mean_qv(bhh, ledger_hist["phase2_input"])
        q_now = calib.monthly_mean_qv(bh1, ledger["phase1_input"])
        gate = calib.k_gate(q_hist, q_now, cfg["calibration"])
        wqh = masses.weekly_heat_value(bhh, ledger_hist["phase2_input"], "p2h")
        # A：历史锚按餐厨档加权（当期餐厨高的周，少借餐厨低的历史年）
        hist_kit = _weekly_kitchen_frac(ledger_hist)
        cur_kit = weekly["kitchen_frac_p1"] if "kitchen_frac_p1" in weekly else None
        weekly["qv_hist_aligned"] = calib.align_hist_weekly(
            weekly.index, wqh["qv_p2h"], cur_kit, hist_kit)
        gate_plant = calib.plant_q_gate(ledger, ledger_hist, cfg["calibration"])
    else:
        gate = {"status": "NO_HIST", "detail": None, "k": 1.0,
                "msg": "未配置二期历史数据或历史台账，k=1，仅一期同周锚。"}
        weekly["qv_hist_aligned"] = float("nan")
        gate_plant = None

    # 第三道闸门：DCS 隐含效率互证（η_dcs = D×Δh/(M×LHV_dcs) vs 反平衡 η）
    gate_eta = calib.eta_implied_gate_from_db(bh2, ledger, cfg)

    k = float(gate.get("k") or 1.0)
    anchor_p1k = weekly["qv_p1"] * k
    weekly["qv_p1k"] = anchor_p1k

    rcfg = cfg["regression"]
    fit_free = regression.fit_free(qmix, alphas, rcfg)
    ci_free = regression.bootstrap_ci(qmix, alphas, rcfg, anchored=False)

    fit_b = regression.fit_anchored(qmix, alphas, anchor_p1k, rcfg)
    ci_b = regression.bootstrap_ci(qmix, alphas, rcfg, anchored=True,
                                   q_msw_anchor=anchor_p1k, anchor_series=anchor_p1k)

    has_hist_anchor = weekly["qv_hist_aligned"].notna().sum() >= 6
    fit_c = ci_c = None
    if has_hist_anchor:
        anchor_c = weekly["qv_hist_aligned"]
        fit_c = regression.fit_anchored(qmix, alphas, anchor_c, rcfg)
        ci_c = regression.bootstrap_ci(qmix, alphas, rcfg, anchored=True,
                                       q_msw_anchor=anchor_c, anchor_series=anchor_c)

    # C：闸门分层 — 蒸汽差但厂内平行更优时，主参考厂内
    primary_code, primary_note = calib.select_primary_report(gate, gate_plant, gate_eta)
    status = gate["status"]
    if primary_code in ("ALERT", "ALERT_ETA"):
        primary = None
        primary_ci = None
        primary_label = "无（ALERT 禁止单锚）"
    elif primary_code == "ALERT_PLANT":
        primary = None
        primary_ci = None
        primary_label = "无单锚（厂内平行供参考）"
    elif primary_code in ("DUAL", "DUAL_PLANT") and ci_c is not None:
        primary = "dual"
        primary_ci = None
        primary_label = ("双锚并报（主参考厂内Q）" if primary_code == "DUAL_PLANT"
                         else "双锚并报")
    else:
        primary = "B"
        primary_ci = ci_b
        primary_label = "解法B 一期同周×k"

    bal_rel = weekly["balance_rel"].abs().mean() if "balance_rel" in weekly else None
    stock_rel = weekly["stock_rel"].abs().mean() if "stock_rel" in weekly else None
    kit_monthly = calib.monthly_kitchen_frac(ledger)

    weekly_out = weekly.copy()
    weekly_out.index.name = "week"
    weekly_out.round(2).to_csv(outdir / "weekly_series.csv", encoding="utf-8-sig")
    weekly_plot = weekly_out.reset_index()

    res = {
        "stats1": stats1, "stats2": stats2, "gate": gate,
        "eff1": bh1.attrs.get("eff_meta", {}), "eff2": bh2.attrs.get("eff_meta", {}),
        "fit_free": fit_free, "ci_free": ci_free,
        "fit_anchored": fit_b, "ci_anchored": ci_b,
        "fit_furnace": fit_c, "ci_furnace": ci_c,
        "anchor": float(anchor_p1k.mean()),
        "anchor_k": k,
        "n_weeks": len(weekly),
        "primary": primary,
        "primary_label": primary_label,
        "primary_code": primary_code,
        "primary_note": primary_note,
        "balance_rel_mean": None if bal_rel is None or (bal_rel != bal_rel) else float(bal_rel),
        "stock_rel_mean": None if stock_rel is None or (stock_rel != stock_rel) else float(stock_rel),
        "gate_plant": gate_plant,
        "gate_eta": gate_eta,
        "kitchen_monthly": None if kit_monthly is None else kit_monthly.to_dict(),
        "dcs2": ts2.attrs.get("dcs_meta"),
        "dcs_hist": None if tsh is None else tsh.attrs.get("dcs_meta"),
    }
    if ts2.attrs.get("source") == "dcs_hourly+daily_fallback":
        ts2.round(4).to_csv(outdir / "phase2_hourly_used.csv", encoding="utf-8-sig")
        ts2.tail(72).round(3).to_csv(outdir / "phase2_hourly_preview.csv", encoding="utf-8-sig")
    if tsh is not None and tsh.attrs.get("source") == "dcs_hourly+daily_fallback":
        tsh.round(4).to_csv(outdir / "phase2_hist_hourly_used.csv", encoding="utf-8-sig")
    (outdir / "report.md").write_text(report.render_report(cfg, res), encoding="utf-8")
    (outdir / "result.json").write_text(
        json.dumps(_result_json(cfg, res), ensure_ascii=False, indent=2), encoding="utf-8")

    plots.fig_weekly(weekly_plot, outdir / "figures" / "weekly.png")
    plots.fig_regression(qmix, weekly["alpha_total"], fit_free, outdir / "figures" / "regression.png")
    plots.fig_screening(bh1, bh2, outdir / "figures" / "screening.png")
    plots.fig_gate(gate.get("detail"), outdir / "figures" / "gate.png")

    print("=" * 62)
    print(f"合格周 {len(weekly)} 个 | 一期有效日 {stats1['valid_days']}/{stats1['total_days']}"
          f" | 二期 {stats2['valid_days']}/{stats2['total_days']}")
    print(f"闸门判定: {gate['status']} — {gate['msg']}")
    print(f"主报口径: {primary_code} — {primary_note}")
    print(f"k = {k:.4f} | 一期同周×k 均值 = {float(anchor_p1k.mean()):.0f} kJ/kg")
    if res["balance_rel_mean"] is not None:
        print(f"物料闭合 |残差|/入炉 周均 = {res['balance_rel_mean']:.1%}")
    if res.get("stock_rel_mean") is not None:
        print(f"库存口径 |入炉-滞后入厂|/入炉 周均 = {res['stock_rel_mean']:.1%}")
    if gate_plant is not None:
        print(f"厂内热值闸门: {gate_plant['status']} δ={gate_plant.get('delta_mean', float('nan')):.1%} k={gate_plant['k']:.4f}")
    print(f"主推: {primary_label}")
    _print_ci("解法B（一期同周×k）", ci_b)
    if ci_c is not None:
        _print_ci("解法C（二期同炉历史周对齐）", ci_c)
    _print_ci("解法A（自由回归，校验）", ci_free)

    truth_path = cfg["paths"].get("truth")
    if truth_path and pathlib.Path(truth_path).exists():
        truth = json.loads(pathlib.Path(truth_path).read_text(encoding="utf-8"))
        print("-" * 62)
        print("真值比对（仅演示数据，解法B）：")
        est = {key: val for key, val in ci_b.items() if not key.startswith("_")}
        for key, tv in truth.items():
            if key in est:
                e = est[key]["mean"]
                print(f"  {key}: 真值 {tv:.0f} | 估计 {e:.0f} | 偏差 {100 * (e - tv) / tv:+.1f}%")
    print("=" * 62)
    print(f"输出目录: {outdir}")
    return 0


def _eta_json(ge):
    if not ge:
        return None
    out = {k: v for k, v in ge.items() if isinstance(v, (str, int, float, bool, type(None)))}
    if "monthly_diff" in out and isinstance(out["monthly_diff"], dict):
        out["monthly_diff"] = {str(k): float(v) for k, v in out["monthly_diff"].items()}
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    sys.exit(main(ap.parse_args().config))
