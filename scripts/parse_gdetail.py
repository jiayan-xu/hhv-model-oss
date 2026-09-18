# -*- coding: utf-8 -*-
"""把地磅日明细编成按日货名吨：ISW / MSW / 出厂。"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "snmis_raw" / "gdetail"
OUT = ROOT / "data" / "plant"

# 固废 = dashboard 过滤口径的货名核（含「其他」且非大件）。
# 大件粉碎 + 生活源/厨余/机扫 = 环卫所生活垃圾。秸秆/固渣不进固废（货名不含「其他」）。
ISW = {
    "其他": "isw_其他",
}
MSW = {
    "其他（大件粉碎垃圾）": "msw_大件",
    "生活源垃圾": "msw_生活",
    "厨余垃圾": "msw_厨余",
    "机扫垃圾": "msw_机扫",
}
TRACK = {
    "秸秆": "isw_秸秆",
    "固渣": "isw_固渣",
}
SKIP = {"/", "炉渣", "飞灰", "城区转运站渗滤液", "填埋场渗滤液", "浓缩液"}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def parse_file(path: Path) -> dict[str, float]:
    wb = load_workbook(path, data_only=True)
    acc: dict[str, float] = defaultdict(float)
    for sn in wb.sheetnames:
        ws = wb[sn]
        phase = "p1" if "一" in sn else "p2"
        for r in range(3, ws.max_row + 1):
            g = ws.cell(r, 5).value
            if g is None:
                continue
            gs = str(g).strip()
            if not gs or gs in SKIP or "合计" in gs:
                continue
            w = _num(ws.cell(r, 9).value)
            if w <= 0:
                continue
            if gs in ISW:
                acc[ISW[gs]] += w
                acc["isw"] += w
            elif gs in MSW:
                acc[MSW[gs]] += w
                acc["msw_wb"] += w
            elif gs in TRACK:
                acc[TRACK[gs]] += w
            else:
                acc["other_unmapped"] += w
                acc[f"raw::{gs}"] += w
            acc[f"{phase}_net"] += w
    acc["date"] = path.stem.replace("gdetail_", "")
    return acc


def main() -> int:
    files = sorted(SRC.glob("gdetail_*.xlsx"))
    if not files:
        raise FileNotFoundError(SRC)
    rows = [parse_file(p) for p in files]
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    cols = ["date", "isw", "isw_其他", "isw_秸秆", "isw_固渣",
            "msw_wb", "msw_生活", "msw_厨余", "msw_机扫", "msw_大件",
            "other_unmapped", "p1_net", "p2_net"]
    for c in cols:
        if c not in df.columns:
            df[c] = 0.0
    df = df[cols].fillna(0.0).sort_values("date")
    OUT.mkdir(parents=True, exist_ok=True)
    dest = OUT / "cargo_daily.csv"
    df.to_csv(dest, index=False, encoding="utf-8-sig")
    print(dest, "days", len(df))
    print(df[["isw", "isw_其他", "msw_大件", "msw_wb", "isw_秸秆", "isw_固渣"]].sum().round(1).to_string())
    unmapped = [c for c in df.columns if c.startswith("raw::")]
    if unmapped:
        print("unmapped cols", unmapped)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
