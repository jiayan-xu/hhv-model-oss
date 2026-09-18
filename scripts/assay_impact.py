"""实测化验数据 vs V4 模型假设的对比分析 + 实测口径经济性测算。

用法：python scripts/assay_impact.py
输入：data/assays/assays_2026.csv（由 parse_assay_reports.py 生成）
输出：控制台对比报告 + data/assays/impact_summary.csv

假设（审查注意）：
- 含水率取该品类化验均值，k_moist=1（检测 LHV 已是该含水收到基）；
- 炉渣率取实际出厂/入炉（data/plant/slag_outbound.csv）；无数则跳过经济性段；
- 处置费沿用 V4 算例价仅为口径对比，非建议价；
- 新品类（沼渣/格栅/其他）按报价分档表给出亏损概率，σ 取入炉热值 8%（单次化验，不确定度放大）。
"""
from __future__ import annotations

import csv
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hhv.pricing import (BatchInput, batch_economics, fixed_cost_per_t,  # noqa: E402
                         load_plant_slag, mean_assay_value, price_tier_table)

# V4 01-固废属性库 默认区间 (kcal/kg)
V4_RANGES = {
    'SW-02 造纸废渣': (800, 1800), 'SW-05 生物质': (2500, 3800),
    'SW-08 干化污泥': (1000, 2000),
}
# V4 三张 04 表算例输入（对比基准）
V4_EXAMPLE = {
    '造纸(其它)': dict(q_lab=1200, moisture_lab=45, ash=35, disposal=80),
    '建装筛上物': dict(q_lab=2800, moisture_lab=15, ash=20, disposal=100),
    '农林': dict(q_lab=3200, moisture_lab=25, ash=8, disposal=60),
}
# 品类 → V4 04 表批次参数（掺烧比例/堆放天数/渗滤液率 与 V4 算例一致）
BATCH_TPL = {
    '造纸(其它)': dict(blend_pct=20, stock_days=5, leachate_rate=0.35),
    '建装筛上物': dict(blend_pct=20, stock_days=5, leachate_rate=0.08),
    '农林': dict(blend_pct=20, stock_days=5, leachate_rate=0.10),
}
DISPOSAL_DEFAULT = 60.0  # 新品类占位处置价（仅演示分档）


def main() -> int:
    src = ROOT / 'data' / 'assays' / 'assays_2026.csv'
    with open(src, encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))

    print('=' * 88)
    print('一、实测化验 vs V4 属性库区间（kcal/kg，湿基低位口径）')
    print('=' * 88)
    for r in rows:
        if not r['lhv_wet_kcal']:
            print(f"{r['month']}月 {r['supplier']:<6} {r['category']:<8}  无热值（仅含水率 {r['moisture_pct']}%）")
            continue
        q = float(r['lhv_wet_kcal'])
        hit = ''
        for name, (lo, hi) in V4_RANGES.items():
            if name.split()[1] in r['category'] or (r['category'] == '沼渣' and '污泥' in name):
                hit = f"{name} {lo}~{hi} → {'区间内' if lo <= q <= hi else ('高于上限' if q > hi else '低于下限')}"
        print(f"{r['month']}月 {r['supplier']:<6} {r['category']:<8}  实测 {q:>6.0f} kcal  {hit}")

    plant = load_plant_slag()
    slag_kw = {"slag_rate_pct": plant["rate_pct"]} if plant else None
    if plant:
        print(f"炉渣率（实际出厂/入炉）{plant['rate_pct']:.2f}%")
    else:
        print("无 data/plant/slag_outbound.csv 有效行，经济性段跳过（热值对比仍输出）")

    print()
    print('=' * 88)
    print('二、实测热值替换 V4 算例 → 边际贡献变化（三张 04 表口径，处置费沿用 V4 算例价）')
    print('=' * 88)
    fixed_pt = fixed_cost_per_t(2560.0, 330.0)
    summary = []
    for v4cat, ex in V4_EXAMPLE.items():
        q_mean = mean_assay_value(rows, 'lhv_wet_kcal', v4cat)
        m_mean = mean_assay_value(rows, 'moisture_pct', v4cat)
        a_mean = mean_assay_value(rows, 'ash_pct', v4cat)
        if q_mean is None or m_mean is None or a_mean is None:
            print(f'{v4cat}: 无完整化验均值')
            continue
        tpl = BATCH_TPL[v4cat]
        n = sum(1 for r in rows if r.get('v4_category') == v4cat and r.get('lhv_wet_kcal'))
        real = BatchInput(
            name=f"{v4cat}·实测均值(n={n})", q_lab=q_mean,
            moisture_lab=m_mean, moisture_furnace=m_mean,
            ash=a_mean, stock_days=tpl['stock_days'], blend_pct=tpl['blend_pct'],
            disposal_price=ex['disposal'], leachate_rate=tpl['leachate_rate'], daily_t=0)
        v4 = BatchInput(
            name=f'{v4cat}·V4算例', q_lab=ex['q_lab'], moisture_lab=ex['moisture_lab'],
            moisture_furnace=ex['moisture_lab'], ash=ex['ash'], stock_days=tpl['stock_days'],
            blend_pct=tpl['blend_pct'], disposal_price=ex['disposal'],
            leachate_rate=tpl['leachate_rate'], daily_t=0)
        e_v4 = batch_economics(v4, fixed_per_t=fixed_pt, v4_compat=True)
        if slag_kw is None:
            print(f"{v4cat:<8} 化验均值 Q={q_mean:.0f} kcal 含水={m_mean:.1f}% 灰分={a_mean:.1f}% "
                  f"(n={n}) | 无炉渣出厂台账，跳过边际")
            summary.append({'v4_category': v4cat, 'q_mean': round(q_mean),
                            'moisture_mean': round(m_mean, 2), 'ash_mean': round(a_mean, 2),
                            'n': n})
            continue
        e_real = batch_economics(real, fixed_per_t=fixed_pt, **slag_kw)
        dq = e_real['q_in'] - e_v4['q_in']
        dm = e_real['margin'] - e_v4['margin']
        print(f"{v4cat:<8} Q入炉 {e_v4['q_in']:>7.0f} → {e_real['q_in']:>7.0f} kcal ({dq:+.0f}) | "
              f"边际 {e_v4['margin']:>7.1f} → {e_real['margin']:>7.1f} 元/t ({dm:+.1f})   "
              f"[含水均值 {m_mean:.1f}% n={n} | 渣率 {e_real['slag_rate']:.1f}%]")
        summary.append({'v4_category': v4cat, 'n': n,
                        'q_in_v4': round(e_v4['q_in']), 'q_in_real': round(e_real['q_in']),
                        'margin_v4': round(e_v4['margin'], 1), 'margin_real': round(e_real['margin'], 1),
                        'delta_margin': round(dm, 1), 'moisture_mean': round(m_mean, 2),
                        'slag_rate': round(e_real['slag_rate'], 2)})

    print()
    print('=' * 88)
    print('三、全来源报价分档（含新品类；σ=8%入炉热值·单次化验口径；处置价为假设档位）')
    print('=' * 88)
    for r in rows:
        if not r['lhv_wet_kcal']:
            continue
        ash = float(r['ash_pct']) if r['ash_pct'] else 20.0
        moist = float(r['moisture_pct']) if r['moisture_pct'] else 30.0
        if slag_kw is None:
            continue
        b = BatchInput(name=f"{r['category']}({r['supplier']})", q_lab=float(r['lhv_wet_kcal']),
                       moisture_lab=moist, moisture_furnace=moist, ash=ash,
                       stock_days=5, blend_pct=20, disposal_price=DISPOSAL_DEFAULT,
                       leachate_rate=0.2, daily_t=0)
        tiers = price_tier_table(b.q_lab, b.q_lab * 0.08, b, [20, 40, 60, 80, 100], **slag_kw)
        cells = '  '.join(f"{t['price']}元:{t['margin_mean']:>6.0f}(亏{t['loss_prob']*100:>4.1f}%)" for t in tiers)
        print(f"{r['category']:<8} {r['supplier']:<6} {cells}")
        summary.append({'category': r['category'], 'source': r['supplier'], 'month': r['month'],
                        'tiers': '|'.join(f"{t['price']}:{t['margin_mean']:.0f}:{t['loss_prob']:.3f}" for t in tiers)})

    out = ROOT / 'data' / 'assays' / 'impact_summary.csv'
    allkeys: list[str] = []
    for d in summary:
        for k in d:
            if k not in allkeys:
                allkeys.append(k)
    with open(out, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=allkeys)
        w.writeheader()
        w.writerows(summary)
    print(f'\nwritten: {out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
