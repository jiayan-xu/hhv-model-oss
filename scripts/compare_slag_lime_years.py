#!/usr/bin/env python
"""年度出渣率 / 石灰用量对比（2024 vs 今年）——数据源：SNMIS 月度生产日报。

版式：data/snmis_raw/dailyReport01_YYYY-MM.xlsx，sheets 一期/二期；
      行=指标（B/C 列标签，子行 B 为空时继承），列=年累计(E)/月累计(F)/1日..31日。
口径：一律取「该月工作簿的年累计(E)」——它才是厂报的年初至今口径。
      注意 2026 年 1 月工作簿缺失，故**不能**用逐月求和代替年累计（会少一个月）；
      只有年累计单元格为空时才回退逐月求和，并在输出里标注。
      全厂 sheet 无炉渣/石灰行 → 全厂 = 一期 + 二期。

  出渣率 = 炉渣产量 ÷ 入炉垃圾量（另附厂报「产率（入厂）%」，其分母是入厂量，口径不同）
  石灰单耗 = 环保耗材出库·石灰 ÷ 入炉垃圾量

用法：
  python scripts/compare_slag_lime_years.py                     # 全年 + 1-8月同期
  python scripts/compare_slag_lime_years.py --window-month 9    # 同期窗口改 9 月
"""
from __future__ import annotations

import argparse
import csv
import pathlib
import sys
import warnings

from openpyxl import load_workbook

warnings.filterwarnings("ignore", category=UserWarning)

ROOT = pathlib.Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "snmis_raw"
OUT = ROOT / "outputs" / "slag_lime_yearly.csv"
SHEETS = ("一期", "二期")

WANT = {
    "inbound": lambda b, c: b.startswith("入厂垃圾量"),
    "furnace": lambda b, c: b.startswith("入炉垃圾量"),
    "slag": lambda b, c: b == "炉渣" and c == "产量",
    "slag_rate_in": lambda b, c: b == "炉渣" and c.startswith("产率（入厂）"),
    "lime": lambda b, c: b == "环保耗材出库" and c == "石灰",
}


def _num(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def find_rows(ws, max_row: int = 140) -> dict[str, int]:
    found: dict[str, int] = {}
    last_b = ""
    for i, row in enumerate(ws.iter_rows(min_row=1, max_row=max_row, max_col=3, values_only=True), 1):
        b = str(row[1]).strip() if len(row) > 1 and row[1] is not None else ""
        if b:
            last_b = b
        b = b or last_b                      # 子行 B 空 → 继承主标签
        c = str(row[2]).strip() if len(row) > 2 and row[2] is not None else ""
        for key, pred in WANT.items():
            if key not in found and pred(b, c):
                found[key] = i
    return found


def read_month(year: int, month: int) -> dict[str, dict] | None:
    """读某年某月工作簿的「年累计」（年初至今）。找不到文件返回 None。"""
    p = RAW / f"dailyReport01_{year}-{month:02d}.xlsx"
    if not p.is_file():
        return None
    wb = load_workbook(p, data_only=True, read_only=True)
    out: dict[str, dict] = {}
    for sheet in SHEETS:
        if sheet not in wb.sheetnames:
            continue
        ws = wb[sheet]
        rows = find_rows(ws)
        if "furnace" not in rows or "slag" not in rows:
            continue
        rec: dict = {"sheet": sheet}
        for key in ("inbound", "furnace", "slag", "lime", "slag_rate_in"):
            rec[key] = _num(ws.cell(rows[key], 5).value) if key in rows else None
        # 任一年累计空缺（含 2024 一期石灰）→ 逐月求和兜底（仅当该年 1..month 工作簿齐全，否则会少算）
        if not rec.get("furnace") or not rec.get("slag") or not rec.get("lime"):
            tot = {"inbound": 0.0, "furnace": 0.0, "slag": 0.0, "lime": 0.0}
            complete = True
            for m in range(1, month + 1):
                pm = RAW / f"dailyReport01_{year}-{m:02d}.xlsx"
                if not pm.is_file():
                    complete = False
                    break
                wsm = load_workbook(pm, data_only=True, read_only=True)[sheet]
                rmm = find_rows(wsm)
                for k in tot:
                    if k in rmm:
                        v = _num(wsm.cell(rmm[k], 6).value)  # 月累计
                        if v:
                            tot[k] += v
            if complete:
                for k, v in tot.items():
                    if not rec.get(k):
                        rec[k] = v or None
                rec["filled_from_monthly"] = True
        out[sheet] = rec
    return out


def total_row(per_sheet: dict[str, dict], year: int, scope: str) -> dict:
    """全厂 = 一期 + 二期（全厂 sheet 无炉渣/石灰行）。"""
    def s2(key):
        vals = [per_sheet[s].get(key) for s in SHEETS if s in per_sheet]
        return sum(v for v in vals if v) or None
    return {"sheet": "全厂(合)", "year": year, "scope": scope,
            "inbound": s2("inbound"), "furnace": s2("furnace"), "slag": s2("slag"),
            "lime": s2("lime"), "slag_rate_in": None,
            "filled_from_monthly": any(per_sheet[s].get("filled_from_monthly") for s in per_sheet if s in per_sheet)}


def build(years: list[int], window_month: int) -> tuple[list[dict], list[dict]]:
    full, ytd = [], []
    for y in years:
        # 全年：优先 12 月簿；没有（今年）则退到最新可用月，并如实标注
        for m in (12, 9, window_month, 8, 7, 6, 5, 4, 3, 2, 1):
            got = read_month(y, m)
            if got:
                label = f"年累计(至{m}月)" if m < 12 else "年累计"
                for s in SHEETS:
                    if s in got:
                        full.append({**got[s], "year": y, "scope": label})
                full.append(total_row(got, y, label))
                break
        # 同期：该年 window_month 月簿的年累计（年初至今到该月）
        got_w = read_month(y, window_month)
        if got_w:
            label = f"1-{window_month}月"
            for s in SHEETS:
                if s in got_w:
                    ytd.append({**got_w[s], "year": y, "scope": label})
            ytd.append(total_row(got_w, y, label))
    return full, ytd


def rate(slag, fur):
    return None if not slag or not fur else round(slag / fur * 100, 2)


def unit(lime, fur):
    return None if not lime or not fur else round(lime * 1000 / fur, 2)


def show(title: str, recs: list[dict], extra_col: bool) -> None:
    print(f"\n=== {title} ===")
    head = f"{'年份':<6}{'线':<8}{'入炉(t)':>12}{'炉渣(t)':>11}{'出渣率%':>9}{'石灰(t)':>10}{'石灰单耗kg/t':>13}"
    print(head + ("   厂报率%(入厂口径)" if extra_col else ""))
    for r in recs:
        line = (f"{r['year']:<6}{r['sheet']:<8}{r['furnace'] or 0:>12,.0f}{r['slag'] or 0:>11,.0f}"
                f"{rate(r['slag'], r['furnace']) or 0:>9.2f}{r['lime'] or 0:>10,.1f}"
                f"{unit(r['lime'], r['furnace']) or 0:>13.2f}")
        if extra_col:
            rep = r.get("slag_rate_in")
            line += f"   {rep:.2f}" if isinstance(rep, (int, float)) else "   —"
        if r.get("filled_from_monthly"):
            line += "   [石灰年累计空缺→逐月合计]"
        print(line)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default="2024,2025,2026")
    ap.add_argument("--window-month", type=int, default=8)
    args = ap.parse_args()
    years = [int(y) for y in args.years.split(",") if y.strip()]

    full, ytd = build(years, args.window_month)
    show("全年口径", full, extra_col=True)
    show(f"同期口径（1-{args.window_month} 月 · 各年取该月年累计）", ytd, extra_col=False)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["window", "year", "scope", "sheet", "inbound_t", "furnace_t", "slag_t",
                    "slag_rate_pct", "slag_rate_reported_pct", "lime_t", "lime_kg_per_t", "note"])
        for recs, win in ((full, "全年"), (ytd, "同期")):
            for r in recs:
                w.writerow([win, r["year"], r["scope"], r["sheet"],
                            r["inbound"], r["furnace"], r["slag"],
                            rate(r["slag"], r["furnace"]), r.get("slag_rate_in"),
                            r.get("lime"), unit(r.get("lime"), r["furnace"]),
                            "石灰年累计空缺→逐月合计" if r.get("filled_from_monthly") else ""])
    print(f"\n已写 {OUT}")
    # 供 PFAiX 效率分析页读取的 JSON（与 efficiency_summary.json 同套路，随发版嵌入）
    import json
    from datetime import datetime
    json_path = ROOT / "outputs" / "config.snmis_daily" / "slag_lime_yearly.json"

    def pack(recs, label):
        by_year = {}
        for r in recs:
            by_year.setdefault(r["year"], {})[r["sheet"]] = {
                "furnace_t": r["furnace"], "slag_t": r["slag"],
                "slag_rate_pct": rate(r["slag"], r["furnace"]),
                "slag_rate_reported_pct": r.get("slag_rate_in"),
                "lime_t": r.get("lime"), "lime_kg_per_t": unit(r.get("lime"), r["furnace"]),
            }
        return {"label": label, "years": [{"year": y, "lines": by_year[y]} for y in sorted(by_year)]}

    payload = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "window_month": args.window_month,
        "ytd": pack(ytd, "1-%d月同期" % args.window_month),
        "full": pack(full, "全年"),
        "notes": [
            "同期口径：各年取该年月工作簿的年累计（厂报年初至今），不用逐月求和——2026 年 1 月工作簿缺失，求和会少一整月",
            "出渣率 = 炉渣出厂量 ÷ 入炉垃圾量；厂报另有「产率（入厂）」口径（分母是入厂量，数值略低）",
            "石灰取「环保耗材出库·石灰」实物量；单耗 = 石灰 ÷ 入炉量",
            "2024 年一期石灰年累计在厂报中空缺，用逐月合计补齐",
            "数据源：SNMIS 月度生产日报（一期/二期 sheet），全厂 = 一期 + 二期",
        ],
    }
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print("已写 " + str(json_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
