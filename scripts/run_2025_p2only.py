# -*- coding: utf-8 -*-
"""2025 只用二期热量 + 2024 纯烧锚（一期指标日报蒸发量吨汽偏低，不解 B）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hhv import balance, calib, config as cfgmod, loader, masses, regression, screening

CFG = "config.demo.yaml"  # 厂内年度配置不入库；本地请指向自己的 config.yaml


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    cfg = cfgmod.load_config(root / CFG)
    sc = cfg["screening"]
    ts2 = loader.load_timeseries(cfg["paths"]["phase2"], cfg["columns"], cfg["units"])
    tsh = loader.load_timeseries(cfg["paths"]["phase2_hist"], cfg["columns"], cfg["units"])
    ledger = loader.load_ledger(cfg["paths"]["ledger"], cfg["ledger_columns"])
    ledger_hist = loader.load_ledger(cfg["paths"]["ledger_hist"], cfg["ledger_columns"])

    def pipe(ts, rated):
        d = screening.screen_hours(ts, sc, rated_flow=rated)
        day = screening.aggregate_days(d, sc)
        day = screening.weekly_admission(day, sc)
        return balance.daily_heat_input(day, cfg["efficiency"], rated)

    bh2 = pipe(ts2, cfg["boilers"]["phase2"]["rated_flow"])
    bhh = pipe(tsh, cfg["boilers"]["phase2"]["rated_flow"])
    stats2 = screening.screening_stats(bh2, sc)
    wmass = masses.weekly_masses(ledger, cfg["masses"])
    walpha = masses.weekly_alpha(wmass)
    alpha_cols = [c for c in walpha.columns if c.startswith("alpha::")]
    extra = [c for c in ("alpha_total", "balance_rel", "stock_rel", "msw_in",
                         "phase2_input") if c in walpha]
    wq2 = masses.weekly_heat_value(bh2, ledger["phase2_input"], "p2")
    wqh = masses.weekly_heat_value(bhh, ledger_hist["phase2_input"], "p2h")
    weekly = wq2.join(walpha[alpha_cols + extra], how="inner")
    weekly["qv_hist_aligned"] = calib.align_hist_weekly(weekly.index, wqh["qv_p2h"])
    weekly = weekly.dropna(subset=["qv_p2", "alpha_total", "qv_hist_aligned"])
    weekly = weekly[weekly["alpha_total"].between(0.005, 0.95)]
    print("weeks", len(weekly), "p2 valid days", stats2["valid_days"], "/", stats2["total_days"])
    qmix = weekly["qv_p2"]
    alphas = weekly[alpha_cols].clip(0, 0.95)
    rcfg = cfg["regression"]
    ci_c = regression.bootstrap_ci(qmix, alphas, rcfg, anchored=True,
                                   q_msw_anchor=weekly["qv_hist_aligned"],
                                   anchor_series=weekly["qv_hist_aligned"])
    ci_a = regression.bootstrap_ci(qmix, alphas, rcfg, anchored=False)
    for tag, ci in (("C", ci_c), ("A", ci_a)):
        print(tag)
        for k, d in ci.items():
            if not str(k).startswith("_"):
                print(f"  {k}: {d['mean']:.0f}  (CI {d['lo']:.0f} ~ {d['hi']:.0f})")
    out = Path(cfg["paths"]["output_dir"]) / "config.snmis_2025"
    out.mkdir(parents=True, exist_ok=True)
    payload = {
        "note": "p2-only + 2024 hist; phase1 meeting steam not used",
        "n_weeks": len(weekly),
        "stats2": stats2,
        "ci_furnace": {k: {kk: float(vv) for kk, vv in v.items()} if isinstance(v, dict) else v
                       for k, v in ci_c.items()},
        "ci_free": {k: {kk: float(vv) for kk, vv in v.items()} if isinstance(v, dict) else v
                    for k, v in ci_a.items()},
        "alpha_mean": float(weekly["alpha_total"].mean()),
        "balance_rel_mean": float(weekly["balance_rel"].abs().mean()) if "balance_rel" in weekly else None,
    }
    (out / "result_p2only.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    weekly.round(2).to_csv(out / "weekly_p2only.csv", encoding="utf-8-sig")
    print("wrote", out / "result_p2only.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
