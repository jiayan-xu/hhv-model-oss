#!/usr/bin/env python
"""效率链汇总导出 → outputs/<cfg>/efficiency_summary.json

供 PFAiX「效率分析」页动态绑定，替代写死在 HTML 里的 86.6%/23.6%/897 天/356,192 行 等数字。

数据来源（均可溯源，随源更新）：
  outputs/softsensor/generation_efficiency_daily.csv   897 天：毛/净发电效率、汽耗率、汽机岛效率、厂用电
  outputs/softsensor/boiler_efficiency_daily.csv       213 天：DCS 隐含锅炉效率
  outputs/config.snmis_daily/result.json               效率互证闸门（η_dcs vs 反平衡 η）
  data/snmis_history.db                                DCS 小时库计数、SNMIS 月数
  data/assays/assays_*.csv                             化验台账份数
  data/plant/ledger_daily.csv                          地磅日明细天数

已知缺口（2026-09-08 审计）：两份 softsensor CSV 的生成脚本已从仓库丢失，其发电量列
（gen_kwh/net_kwh）的原始来源是 SNMIS 生产日报 xlsx（未入库）。重建前本汇总只能随现有
CSV 更新——见 README「已知缺口」。

用法：python scripts/export_efficiency_summary.py [--out-dir outputs/config.snmis_daily]
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import pathlib
import sqlite3
import statistics
import sys
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _rows(path: pathlib.Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _f(row: dict, key: str) -> float | None:
    try:
        v = float(row[key])
        return v if math.isfinite(v) else None  # 过滤 NaN/Inf
    except (KeyError, TypeError, ValueError):
        return None


def _stats(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "mean": None, "p05": None, "p95": None}
    s = sorted(values)
    n = len(s)
    q = lambda p: s[min(n - 1, max(0, int(round(p * (n - 1)))))]  # noqa: E731
    return {"n": n, "mean": statistics.mean(s), "p05": q(0.05), "p95": q(0.95)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", type=pathlib.Path,
                    default=ROOT / "outputs" / "config.snmis_daily")
    args = ap.parse_args()

    gen_csv = ROOT / "outputs" / "softsensor" / "generation_efficiency_daily.csv"
    blr_csv = ROOT / "outputs" / "softsensor" / "boiler_efficiency_daily.csv"
    if not gen_csv.is_file() or not blr_csv.is_file():
        sys.exit(f"[efficiency] 缺少 softsensor 产物：{gen_csv.name} / {blr_csv.name}")

    gen = _rows(gen_csv)
    blr = _rows(blr_csv)

    gross = _stats([v for r in gen if (v := _f(r, "eta_gen_gross")) is not None])
    net = _stats([v for r in gen if (v := _f(r, "eta_gen_net")) is not None])
    turb = _stats([v for r in gen if (v := _f(r, "eta_turbine_gross")) is not None])
    srate = _stats([v for r in gen if (v := _f(r, "steam_rate")) is not None])
    aux = _stats([v for r in gen if (v := _f(r, "aux_pct")) is not None])
    eta_dcs = _stats([v for r in blr if (v := _f(r, "eta_dcs")) is not None])
    # 厂用电率按发电量加权（简单日均值会被低发电日抬高，不是厂里的实际口径）
    tot_gen = sum(v for r in gen if (v := _f(r, "gen_kwh")) is not None)
    tot_net = sum(v for r in gen if (v := _f(r, "net_kwh")) is not None)
    aux_weighted = (1 - tot_net / tot_gen) * 100 if tot_gen else None

    # 厂用电季节区间：按月均取 min/max（页面口径「冬低夏高」）
    by_month: dict[str, list[float]] = {}
    for r in gen:
        v = _f(r, "aux_pct")
        m = r.get("month")
        if v is not None and m:
            by_month.setdefault(m, []).append(v)
    monthly_aux = [statistics.mean(v) for v in by_month.values()]

    dates = [r["date"] for r in gen if r.get("date")]
    window = [min(dates), max(dates)] if dates else [None, None]

    # 互证闸门（result.json）
    gate = {}
    rj = args.out_dir / "result.json"
    if rj.is_file():
        r = json.loads(rj.read_text(encoding="utf-8"))
        ge = r.get("gate_eta") or {}
        gate = {"status": ge.get("status"), "n_days": ge.get("n_days"),
                "eta_dcs_mean": ge.get("eta_dcs_mean"), "eta_rb_mean": ge.get("eta_rb_mean")}

    # 数据源计数
    sources: dict = {}
    db = ROOT / "data" / "snmis_history.db"
    if db.is_file():
        c = sqlite3.connect(db)
        sources["dcs_hours"] = c.execute("SELECT COUNT(*) FROM hourly_hourly").fetchone()[0]
        sources["snmis_months"] = c.execute(
            "SELECT COUNT(DISTINCT substr(date,1,7)) FROM daily WHERE phase='p2'").fetchone()[0]
        c.close()
    assay_files = sorted((ROOT / "data" / "assays").glob("assays_*.csv"))
    sources["assays"] = sum(len(_rows(p)) for p in assay_files)
    led = ROOT / "data" / "plant" / "ledger_daily.csv"
    if led.is_file():
        sources["weighbridge_days"] = len(_rows(led))

    out = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "window": {"start": window[0], "end": window[1], "days": len(gen)},
        "boiler": {
            "eta_rb_mean": gate.get("eta_rb_mean"),   # 反平衡 η（页面主口径 86.6%）
            "eta_dcs_mean": eta_dcs["mean"], "n_days": eta_dcs["n"],
            "gate": gate,
            "note": "主口径取反平衡 η 均值；DCS 隐含 η 日均值用于互证",
        },
        "turbine": {
            "eta_mean": turb["mean"], "steam_rate_mean": srate["mean"], "n_days": turb["n"],
            "note": "汽机岛效率 = 3600 ÷ (汽耗率 × 焓差 2665 kJ/kg)；含 SNCR/吹灰辅助用汽约 6%",
        },
        "gross": gross,
        "net": {
            **net,
            "aux_mean": aux_weighted,          # 发电量加权
            "aux_daily_mean": aux["mean"],     # 日均（仅供参考）
            "aux_lo": min(monthly_aux) if monthly_aux else None,
            "aux_hi": max(monthly_aux) if monthly_aux else None,
        },
        "sources": sources,
        "maintenance_split": {
            "available": False,
            "note": "三炉/两炉检修分组来自真实检修记录（一次性分析，2026-09-07），"
                    "数据库 n_furnaces_on 无区分，未随月度跑批更新",
        },
    }

    args.out_dir.mkdir(parents=True, exist_ok=True)
    dst = args.out_dir / "efficiency_summary.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[efficiency] 已写 {dst}")
    b = out["boiler"]["eta_rb_mean"] or out["boiler"]["eta_dcs_mean"]
    print(f"  锅炉(反平衡) {b:.4f}（互证 {out['boiler']['gate'].get('n_days')} 天） | "
          f"汽机岛 {out['turbine']['eta_mean']:.4f} | 毛 {out['gross']['mean']:.4f} | "
          f"净 {out['net']['mean']:.4f} | 汽耗率 {out['turbine']['steam_rate_mean']:.2f} | "
          f"厂用电(加权) {out['net']['aux_mean']:.2f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
