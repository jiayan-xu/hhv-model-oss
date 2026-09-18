"""解析 E:/一般固废/样品检测报告/ 的月度 PDF 检测报告 → 结构化化验台账。

用法：python scripts/parse_assay_reports.py [--src E:/一般固废/样品检测报告]
输出：data/assays/assays_2026.csv（一报告一行，追加式；同文件重跑覆盖旧行）

口径说明：
- 湿基低位热值 = 收到基低位热值（采样时点），即定价/反推模型所需口径；
- 含水率：报告直接给出的用实测；缺失时由 干基高位/湿基高位 之比反推
  （M = 1 − HHV_wet/HHV_dry），method 列标注 derived；
- 灰分缺失时按干基元素质量平衡估算（100−C−H−O−N−S），折算到收到基，method 标注 derived；
- 3 月报告热值指数上标在文本层丢失，按 干基≥湿基≥低位 且物理合理 自动定阶。
"""
from __future__ import annotations

import argparse
import csv
import pathlib
import re
import sys

import pdfplumber

KJ2KCAL = 1 / 4.1868
ROOT = pathlib.Path(__file__).resolve().parents[1]

# 文件名 → (供应商, 品类, V4品类映射)
CAT_MAP = [
    (r'理文', '理文', '造纸底渣', '造纸(其它)'),
    (r'雷博尔', '雷博尔', '农林垃圾', '农林'),
    (r'苏再投', '太仓苏再投', '装修垃圾', '建装筛上物'),
    (r'天越', '天越', '装修垃圾', '建装筛上物'),
    (r'华衍', '华衍', '沼渣', '沼渣(新)'),
    (r'苏水中法', '苏水中法', '格栅垃圾', '格栅垃圾(新)'),
    (r'东升', '东升', '其他工业固废', '其他固废(新)'),
    # 2025 年厂内自检报告（一期/二期生活垃圾、建筑垃圾）
    (r'一期生活垃圾', '常熟浦发·一期', '生活垃圾(一期)', '生活垃圾(一期)'),
    (r'二期生活垃圾', '常熟浦发·二期', '生活垃圾(二期)', '生活垃圾(二期)'),
    (r'建筑垃圾', '常熟浦发', '建筑垃圾', '建装筛上物(建筑垃圾)'),
]


def classify(fname: str):
    for pat, sup, cat, v4 in CAT_MAP:
        if re.search(pat, fname):
            return sup, cat, v4
    return '未知', '未知', '未知'


def sci_value(num: float, exp_str: str | None) -> float:
    """恢复科学计数：×10³ 文本层为 ×103（取末位数字为阶）；×10 阶丢失则按物理合理定阶。"""
    if exp_str:
        return num * 10 ** int(exp_str)
    # 阶丢失：干基高位只可能是 10^4（固废 HHV 5~40 MJ/kg）量级
    return num * 10 ** 4


def parse_report(pdf_path: pathlib.Path) -> dict | None:
    out = {'file': pdf_path.name}
    m = re.search(r'(\d{4})年(\d+)月固废检测报告-(.+)\.pdf', pdf_path.name)
    if not m:
        # 2025 命名式：“7月，一期生活垃圾。委托单位+编号.pdf”；年份取自父目录或文件名
        m2 = re.search(r'(\d{1,2})月，(.+?)。', pdf_path.name)
        if m2:
            ym = re.search(r'(20\d{2})年', str(pdf_path.parent)) or re.search(r'(20\d{2})年', pdf_path.name)
            out['year'] = ym.group(1) if ym else ''
            out['month'] = m2.group(1)
        else:
            out['year'] = out['month'] = ''
    else:
        out['year'] = m.group(1)
        out['month'] = m.group(2)
    out['supplier'], out['category'], out['v4_category'] = classify(pdf_path.name)
    fields: dict[str, float] = {}
    sampled = None
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            t = page.extract_text() or ''
            m2 = re.search(r'接样日期\s+([\d-]+)', t)
            if m2:
                sampled = m2.group(1)
            # 适配跨行断开的项目名：“干基高位\n热值” → “干基高位热值”
            t2 = re.sub(r'(干基高位|湿基高位|湿基低位)\s*\n\s*热值', r'\1热值', t)
            # 热值行：全文归一化后弹性匹配（项目名与数值间允许隔“样品编号/热值”等 ≤2 个词）
            t_flat = re.sub(r'\s+', ' ', t2)
            for mh in re.finditer(
                    r'(干基高位|湿基高位|湿基低位)(?:热值)?\s*(?:\S+\s+){0,2}([\d.]+)\s*×\s*10([⁰¹²³⁴\d]*)\s*kJ', t_flat):
                key, num, exp = mh.group(1) + '热值', float(mh.group(2)), mh.group(3).strip() or None
                exp = str(int(exp)) if exp and exp.isdigit() and len(exp) == 1 else exp
                fields[key] = sci_value(num, exp if exp and exp[0] in '345' else None)
            for line in t2.splitlines():
                # 单值百分比行（兼容基标注后缀：灰分（干基）/含水率（收到基）等）
                mp = re.search(r'(灰分|含水率|硫|碳|氢|氧|氮)（?(?:干基|收到基|湿基)?）?\s+\S+\s+([\d.]+)\s*%', line)
                if mp:
                    fields.setdefault(mp.group(1), float(mp.group(2)))
                    if mp.group(1) == '灰分':
                        basis = mp.group(0)
                        fields['灰分_basis'] = '干基' if '干基' in basis else ('收到基' if '收到基' in basis else '')
    if not fields:
        return None
    out['sampled_date'] = sampled or ''
    hhv_d = fields.get('干基高位热值')
    hhv_w = fields.get('湿基高位热值')
    lhv_w = fields.get('湿基低位热值')
    out['hhv_dry_kJ'] = hhv_d
    out['hhv_wet_kJ'] = hhv_w
    out['lhv_wet_kJ'] = lhv_w
    out['lhv_wet_kcal'] = round(lhv_w * KJ2KCAL, 0) if lhv_w else None
    # 含水率：实测优先，缺失用 HHV 比值反推
    moist = fields.get('含水率')
    method = 'reported'
    if moist is None and hhv_d and hhv_w and hhv_d > 0:
        moist = (1 - hhv_w / hhv_d) * 100
        method = 'derived'
    out['moisture_pct'] = round(moist, 2) if moist is not None else None
    out['moisture_method'] = method if moist is not None else ''
    # 灰分：缺失用干基元素质量平衡估算 → 折收到基
    ash = fields.get('灰分')
    a_method = 'reported'
    if ash is None and all(fields.get(k) is not None for k in ('碳', '氢', '氧', '氮', '硫')) and moist is not None:
        ash_dry = 100 - sum(fields[k] for k in ('碳', '氢', '氧', '氮', '硫'))
        ash = ash_dry * (1 - moist / 100)
        a_method = 'derived'
    out['ash_pct'] = round(ash, 2) if ash is not None else None
    out['ash_method'] = a_method if ash is not None else ''
    out['ash_basis'] = fields.get('灰分_basis', '')
    for k_src, k_dst in (('碳', 'C_pct'), ('氢', 'H_pct'), ('氧', 'O_pct'), ('氮', 'N_pct'), ('硫', 'S_pct')):
        out[k_dst] = fields.get(k_src)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default='F:/一般固废/样品检测报告')
    ap.add_argument('--out', default=None, help='输出 CSV（默认按源目录年份自动命名）')
    args = ap.parse_args()
    src = pathlib.Path(args.src)
    outcsv = pathlib.Path(args.out) if args.out else (
        ROOT / 'data' / 'assays' / f'assays_{args.src.rstrip("/").split("/")[-1].replace("年", "")}.csv')
    rows = []
    for f in sorted(src.glob('*.pdf')):
        try:
            r = parse_report(f)
        except Exception as e:
            print(f'[WARN] {f.name}: {e}')
            continue
        if r:
            rows.append(r)
            print(f"{r['month']}月 {r['supplier']:<6} {r['category']:<8} 接样{r['sampled_date']} | "
                  f"LHV {r['lhv_wet_kJ'] or '—':>7} kJ/kg ({r['lhv_wet_kcal'] or '—'} kcal) | "
                  f"灰分 {r['ash_pct'] or '—':>6}({r['ash_method'] or '—':>7}) | "
                  f"水分 {r['moisture_pct'] or '—':>6}({r['moisture_method'] or '—':>7}) | "
                  f"S {r['S_pct'] if r['S_pct'] is not None else '—'}%")
        else:
            print(f"{f.name}: 未解析出检测结果")
    outdir = outcsv.parent
    outdir.mkdir(parents=True, exist_ok=True)
    cols = ['year', 'month', 'supplier', 'category', 'v4_category', 'sampled_date', 'file',
            'lhv_wet_kJ', 'lhv_wet_kcal', 'hhv_dry_kJ', 'hhv_wet_kJ',
            'ash_pct', 'ash_basis', 'ash_method', 'moisture_pct', 'moisture_method',
            'C_pct', 'H_pct', 'O_pct', 'N_pct', 'S_pct']
    with open(outcsv, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f'\nwritten: {outcsv} ({len(rows)} 行)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
