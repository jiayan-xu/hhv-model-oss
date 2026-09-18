"""生成市场化垃圾定价建议 Excel（V4.1 引擎输出）。

用法：
  python scripts/build_pricing_report.py                    # 用 V4 算例输入
  python scripts/build_pricing_report.py --qsd-pct 6.0      # 热值不确定度(1σ, 占入炉热值%)
输出：outputs/pricing/市场化垃圾定价建议_V4.1.xlsx
"""
from __future__ import annotations

import argparse
import pathlib
import sys
from datetime import datetime

XLSX_SKILL = ""  # 可选：本机 xlsx skill 路径；可用环境变量 XLSX_SKILL 指定
for p in (XLSX_SKILL, XLSX_SKILL + "/templates"):
    if p not in sys.path:
        sys.path.insert(0, p)

from openpyxl import Workbook  # noqa: E402
from openpyxl.styles import Alignment, Font  # noqa: E402
from base import (ACCENT_NEGATIVE, ACCENT_POSITIVE, fill_data_row, fill_header,  # noqa: E402
                  fill_total, font_body, font_header, font_title, setup_sheet,
                  style_header_row, style_total_row)

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from hhv.pricing import (BatchInput, batch_economics, fixed_cost_per_t,  # noqa: E402
                         load_plant_slag, load_pricing_calibration, margin_distribution,
                         portfolio, price_tier_table,
                         shutdown_economics)

# ── 输入：与同事 V4 三张 04 表逐项一致 ──────────────────────────────
BATCHES = [
    BatchInput("造纸(其它)", 1200, 45, 50, 35, 5, 20, 80, 0, 0, 0.35, 150),
    BatchInput("建装筛上物", 2800, 15, 18, 20, 5, 20, 100, 0, 0, 0.08, 180),
    BatchInput("农林", 3200, 25, 28, 8, 5, 20, 60, 0, 0, 0.10, 130),
]
PRICES = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
OUT = pathlib.Path(__file__).resolve().parents[1] / "outputs" / "pricing"
OUT.mkdir(parents=True, exist_ok=True)


def put(ws, row, col, val, fmt=None, header=False, total=False, bold=False):
    cell = ws.cell(row=row, column=col, value=val)
    if header:
        cell.fill = fill_header(); cell.font = font_header()
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    elif total:
        cell.fill = fill_total(); cell.font = Font(bold=True)
    else:
        cell.fill = fill_data_row(row)
        cell.font = Font(bold=bold)
    if fmt:
        cell.number_format = fmt
    return cell


def section(ws, row, text, last_col):
    c = ws.cell(row=row, column=1, value=text)
    c.font = font_title()
    return row + 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--qsd-pct", type=float, default=6.0,
                    help="热值不确定度 1σ，占入炉热值%%（hhv 反推 CI 收紧后可调小）")
    ap.add_argument("--v4", action="store_true",
                    help="用 V4 灰分渣率+含水差修正（算例对照）；默认厂内口径")
    ap.add_argument("--slag-out", type=float, default=None, help="炉渣出厂合计 t")
    ap.add_argument("--furnace-input", type=float, default=None, help="同期入炉合计 t")
    args = ap.parse_args()
    qsd_frac = args.qsd_pct / 100.0

    slag_kw: dict = {}
    slag_note = ""
    if args.v4:
        slag_kw = {"v4_compat": True}
        slag_note = "V4 兼容：灰分>30%→55% 否则 50%；含水做检测/入炉差修正"
    else:
        # 厂内口径统一用实测校准（data/pricing_calibration.yaml），与 PFAiX 定价页同一套数；
        # --v4 对照路径不注入校准，保持与 V4 Excel 逐格可对。
        cal = load_pricing_calibration()
        if not cal:
            print("⚠ 未找到 data/pricing_calibration.yaml——将按 V4 假设值（eta 78~81%、汽耗率 4.8）计算，"
                  "与 PFAiX 定价页口径不一致，对外报价前请先恢复校准文件")
        if args.slag_out is not None and args.furnace_input is not None:
            from hhv.pricing import actual_slag_rate
            rate = actual_slag_rate(args.slag_out, args.furnace_input)
            slag_kw = {"slag_rate_pct": rate, "calibration": cal}
            slag_note = (f"实际出厂 {args.slag_out:g} t / 入炉 {args.furnace_input:g} t "
                         f"= {rate:.2f}%")
        else:
            plant = load_plant_slag()
            if plant is None:
                print("厂内口径需要炉渣出厂量：填写 data/plant/slag_outbound.csv "
                      "或传入 --slag-out 与 --furnace-input。对照 V4 算例请加 --v4")
                return 1
            slag_kw = {"slag_rate_pct": plant["rate_pct"], "calibration": cal}
            slag_note = (f"实际出厂 {plant['slag_out_t']:.1f} t / 入炉 {plant['furnace_input_t']:.1f} t "
                         f"= {plant['rate_pct']:.2f}%")

    days = 330.0
    total_burn = 2100.0 + 2300.0 * 0.20
    fixed_pt = fixed_cost_per_t(total_burn, days)
    ecoms = [batch_economics(b, fixed_per_t=fixed_pt, **slag_kw) for b in BATCHES]
    port = portfolio(BATCHES, ecoms)
    shut = shutdown_economics(weighted_margin=port["margin"])

    wb = Workbook()

    # ══ Sheet1 单批次测算 ══
    ws = wb.active
    ws.title = "01-单批次测算"
    setup_sheet(ws, title="单批次经济测算（厂内：实际炉渣出厂/入炉；含水取化验均值）", last_col=5)
    row = 3
    row = section(ws, row, "一、输入（与 V4 三张 04 表一致）", 5)
    heads = ["指标", "单位", "造纸(其它)", "建装筛上物", "农林"]
    for j, h in enumerate(heads, 1):
        put(ws, row, j, h, header=True)
    ws.row_dimensions[row].height = 22
    row += 1
    in_rows = [("检测低位热值", "kcal/kg", "q_lab", "0"), ("检测含水率", "%", "moisture_lab", "0"),
               ("入炉含水率", "%", "moisture_furnace", "0"), ("灰分", "%", "ash", "0"),
               ("堆放天数", "天", "stock_days", "0"), ("掺烧比例", "%", "blend_pct", "0"),
               ("处置费单价", "元/t", "disposal_price", "0.00"), ("渗滤液产生率", "吨/吨", "leachate_rate", "0.00"),
               ("日处置量", "t/d", "daily_t", "0")]
    for label, unit, key, fmt in in_rows:
        put(ws, row, 1, label); put(ws, row, 2, unit)
        for j, b in enumerate(BATCHES, 3):
            put(ws, row, j, getattr(b, key), fmt)
        row += 1
    row += 1
    row = section(ws, row, "二、修正与能量链", 5)
    mid_rows = [("堆放损耗 k", "", "k_stock", "0.000"), ("含水率修正 k", "", "k_moist", "0.000"),
                ("灰分修正 k", "", "k_ash", "0.00"), ("入炉热值", "kcal/kg", "q_in", "0.0"),
                ("锅炉效率", "%", "eta", "0.0"), ("吨发电量", "kwh/t", "gen", "0.0"),
                ("上网电量", "kwh/t", "gen_net", "0.0"), ("上网发电收入", "元/t", "power_rev", "0.00"),
                ("炉渣率", "%", "slag_rate", "0.00"), ("炉渣收入", "元/t", "slag_rev", "0.00")]
    for label, unit, key, fmt in mid_rows:
        put(ws, row, 1, label); put(ws, row, 2, unit)
        for j, e in enumerate(ecoms, 3):
            put(ws, row, j, e[key], fmt)
        row += 1
    row += 1
    row = section(ws, row, "三、成本与结果（决策指标）", 5)
    res_rows = [("变动成本合计", "元/t", "var_cost", "0.00"),
                ("边际贡献(含处置费)", "元/t", "margin", "0.00"),
                ("边际贡献(不含处置费)", "元/t", "margin_ex_disposal", "0.00"),
                ("吨固定成本(摊焚烧总量)", "元/t", "fixed_per_t", "0.00"),
                ("全成本净利", "元/t", "full_cost_profit", "0.00"),
                ("盈亏平衡处置价", "元/t", "be_price", "0.00"),
                ("临界热值·燃料口径(V4)", "kcal/kg", "q_crit_fuel", "0"),
                ("临界热值·含处置费(修正)", "kcal/kg", "q_crit_incl", "0")]
    for label, unit, key, fmt in res_rows:
        total = label.startswith("边际贡献(含处置费)") or label.startswith("全成本")
        put(ws, row, 1, label, total=total); put(ws, row, 2, unit, total=total)
        for j, e in enumerate(ecoms, 3):
            put(ws, row, j, e[key], fmt, total=total)
        row += 1

    # ══ Sheet2 组合与年度 ══
    ws2 = wb.create_sheet("02-组合与年度")
    setup_sheet(ws2, title="组合加权 · 停炉经济性 · 年度聚合（P1-1/P1-3 修正：全部按实际组合量）", last_col=3)
    row = 3
    row = section(ws2, row, "一、组合加权（权重=日处置量，合计 "
                            f"{port['total_daily_t']:.0f} t/d）", 3)
    for label, key, fmt in [("加权边际贡献", "margin", "0.00"), ("加权入炉热值", "q_in", "0.0"),
                            ("加权吨发电量", "gen", "0.0"), ("加权上网发电收入", "power_rev", "0.00"),
                            ("加权处置费", "disposal", "0.00"), ("加权耗材成本", "consumables", "0.00")]:
        put(ws2, row, 1, label); put(ws2, row, 2, port[key], fmt); put(ws2, row, 3, "元/t" if key != "q_in" else "kcal/kg")
        row += 1
    row += 1
    row = section(ws2, row, "二、停炉经济性（五分法，六炉四机）", 3)
    for opt in shut["options"]:
        put(ws2, row, 1, f"{opt['name']} 隐性损失")
        put(ws2, row, 2, opt["loss_total"], "0.0")
        put(ws2, row, 3, "万元/年（固定闲置 %.0f+启停 %.0f+保养 %.0f+待机 %.0f+寿命 %.0f）"
            % (opt["idle"], opt["start"], opt["maint"], opt["standby"], opt["life"]))
        row += 1
    row += 1
    row = section(ws2, row, "三、协同焚烧增量价值", 3)
    for label, key, fmt in [("协同固废增量边际贡献", "increment_margin", "0.0"),
                            ("避免停炉损失(取损失较小方案)", "avoided_loss", "0.0"),
                            ("协同增量总价值", "synergy_total", "0.0")]:
        total = key == "synergy_total"
        put(ws2, row, 1, label, total=total)
        put(ws2, row, 2, shut[key], fmt, total=total)
        put(ws2, row, 3, "万元/年", total=total)
        row += 1

    # ══ Sheet3 报价分档 ══
    ws3 = wb.create_sheet("03-报价分档")
    setup_sheet(ws3, title=f"报价分档亏损概率（热值 1σ = 入炉热值 × {args.qsd_pct:g}%；"
                           "接 hhv 反推 CI 后按实际 σ 重算）", last_col=8)
    row = 3
    for b, e in zip(BATCHES, ecoms):
        q_mean, q_sd = e["q_in"], e["q_in"] * qsd_frac
        tiers = price_tier_table(q_mean, q_sd, b, PRICES, **slag_kw)
        row = section(ws3, row, f"{b.name}｜入炉热值 {q_mean:.0f} kcal/kg（σ={q_sd:.0f}）"
                                f"｜盈亏平衡处置价 {e['be_price']:.1f} 元/t", 8)
        heads = ["处置价 元/t", "期望边际 元/t", "P5 边际 元/t", "亏损概率", "判定"]
        for j, h in enumerate(heads, 1):
            put(ws3, row, j, h, header=True)
        ws3.row_dimensions[row].height = 20
        row += 1
        for t in tiers:
            put(ws3, row, 1, t["price"], "0")
            put(ws3, row, 2, t["margin_mean"], "0.00")
            put(ws3, row, 3, t["margin_p5"], "0.00")
            put(ws3, row, 4, t["loss_prob"], "0.0%")
            cell = put(ws3, row, 5, "高风险" if t["loss_prob"] > 0.2 else
                       ("关注" if t["loss_prob"] > 0.05 else "安全"))
            if t["loss_prob"] > 0.2:
                cell.font = Font(color=ACCENT_NEGATIVE, bold=True)
            elif t["loss_prob"] <= 0.05:
                cell.font = Font(color=ACCENT_POSITIVE)
            row += 1
        row += 1

    # ══ Sheet4 口径说明 ══
    ws4 = wb.create_sheet("04-口径说明")
    setup_sheet(ws4, title="V4.1 引擎与同事 V4 Excel 的差异与口径", last_col=2)
    notes = [
        ("验证基准", "引擎通过 V4 文件「12-算例结果速览」13 项静态值逐格验证（容差内全等），"
                     "运行 scripts/validate_pricing.py 可复验。"),
        ("P1-1 修正", "V4 的 05 表手填日进厂量 100 t/d 与组合 460 t/d 脱钩；引擎一律使用实际加权量，"
                      "总览口径不再矛盾。"),
        ("P1-2 修正", "临界热值双口径并列：燃料口径（=V4 原公式，处置费按 0 计）与含处置费口径"
                      "（定价建议采用）。造纸含处置费口径约 36 kcal/kg，V4 口径约 1023 kcal/kg。"),
        ("P1-3 修正", "掺烧整体测算改用实际组合量；2300×20% 的上限值仅做校验。"),
        ("效率口径", ("实测校准：锅炉效率 86.6%、汽耗率 5.78 kg/kWh（897 天实测，来源 "
                    "data/pricing_calibration.yaml）。V4 假设值 78~81%/4.8 仅用于 --v4 对照，"
                    "禁止用于对外报价。前端定价页 OP 表由同一校准文件生成。")
                   if not args.v4 else
                   "V4 兼容口径：锅炉效率 78~81%、汽耗率 4.8（仅用于与 V4 Excel 逐格对照）。"),
        ("稳健性", "全链路除法带保护；掺烧比例超 20% 显式告警并按上限档取参（V4 静默取档）。"),
        ("概率层", f"热值按正态 N(入炉热值, σ={args.qsd_pct:g}%) 抽样；σ 接入 hhv 反推置信区间后为实测值。"
                   "边际对热值严格线性，解析解与蒙特卡洛互为校验。"),
        ("炉渣率", slag_note),
        ("含水率", "厂内口径只取化验均值，k_moist=1（检测 LHV 已是该含水收到基）。"
                 "--v4 时才做检测含水 vs 入炉含水差修正。"),
        ("生成信息", f"{datetime.now():%Y-%m-%d %H:%M} · hhv-model pricing engine v4.1 · 数值为程序计算值"),
    ]
    for k, v in notes:
        put(ws4, row, 1, k, bold=True)
        c = put(ws4, row, 2, v)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        ws4.row_dimensions[row].height = 30
        row += 1
    for col, w in ((1, 26), (2, 80)):
        ws4.column_dimensions[chr(64 + col)].width = w

    out = OUT / "市场化垃圾定价建议_V4.1.xlsx"
    wb.save(out)
    print("written:", out)
    print(f"炉渣口径: {slag_note}")
    print(f"\n组合加权边际 {port['margin']:.1f} 元/t｜协同增量总价值 {shut['synergy_total']:.0f} 万元/年")
    for b, e in zip(BATCHES, ecoms):
        tiers = price_tier_table(e["q_in"], e["q_in"] * qsd_frac, b, PRICES, **slag_kw)
        safe = next(t["price"] for t in reversed(tiers) if t["loss_prob"] > 0.05) \
            if any(t["loss_prob"] > 0.05 for t in tiers) else tiers[0]["price"]
        print(f"  {b.name}: 边际 {e['margin']:.1f} 元/t | 亏损概率>5% 的价格下限 ≈ {safe} 元/t")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
