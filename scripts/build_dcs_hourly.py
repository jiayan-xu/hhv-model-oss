"""把 DCS 小时库拼成二期小时 CSV（检查用）。主链 run.py 直接读库，不依赖本脚本。

用法：python scripts/build_dcs_hourly.py
输出：data/plant/phase2_hourly.csv
      data/plant/phase2_hist_hourly.csv
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hhv import config as cfgmod, loader  # noqa: E402


def main() -> int:
    cfg = cfgmod.load_config(ROOT / "config.snmis_daily.yaml")
    units, dcs_cfg = cfg["units"], cfg["dcs"]
    db = ROOT / cfg["paths"]["dcs_hourly_db"]
    out = ROOT / "data" / "plant"

    ts2 = loader.load_timeseries(ROOT / cfg["paths"]["phase2"], cfg["columns"], units)
    tsh = loader.load_timeseries(ROOT / cfg["paths"]["phase2_hist"], cfg["columns"], units)
    led = loader.load_ledger(ROOT / cfg["paths"]["ledger"], cfg["ledger_columns"])
    ledh = loader.load_ledger(ROOT / cfg["paths"]["ledger_hist"], cfg["ledger_columns"])

    h2 = loader.load_dcs_phase(db, ts2, units, led.index.min(), led.index.max(), dcs_cfg)
    hh = loader.load_dcs_phase(db, tsh, units, ledh.index.min(), ledh.index.max(), dcs_cfg)
    h2.to_csv(out / "phase2_hourly.csv", encoding="utf-8-sig")
    hh.to_csv(out / "phase2_hist_hourly.csv", encoding="utf-8-sig")
    print("phase2", h2.attrs.get("dcs_meta"))
    print("hist  ", hh.attrs.get("dcs_meta"))
    print("written", out / "phase2_hourly.csv", out / "phase2_hist_hourly.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
