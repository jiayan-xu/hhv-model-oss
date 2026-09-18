"""定价引擎逐格验证：对 V4 文件 12-算例结果速览 的 13 项静态值 diff。

通过标准：全部 |差| < 容差。任何 FAIL 都意味着引擎与同事的 Excel 语义不一致，
不得用于定价，先查公式。
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hhv.pricing import BatchInput, batch_economics, fixed_cost_per_t, shutdown_economics  # noqa: E402

# 04-造纸(其它) 输入（与 V4 完全一致）
PAPER = BatchInput(
    name="造纸(其它)", q_lab=1200.0, moisture_lab=45.0, moisture_furnace=50.0,
    ash=35.0, stock_days=5.0, blend_pct=20.0, disposal_price=80.0,
    purchase_price=0.0, transport_price=0.0, leachate_rate=0.35, daily_t=150.0,
)

# 13 表链条 → 吨固定成本（V4: 03!B28 = 13!B10×B11/10000, B29 = 4000万/84.48万吨）
TOTAL_BURN = 2100.0 + 2300.0 * 0.20   # 2560 t/d
DAYS = 330.0
FIXED_PT = fixed_cost_per_t(TOTAL_BURN, DAYS)

ec = batch_economics(PAPER, fixed_per_t=FIXED_PT, v4_compat=True)

# (指标, 引擎值, V4 12表静态值, 容差)
CHECKS = [
    ("入炉热值",            ec["q_in"],               994.7,  0.15),
    ("吨垃圾发电量",        ec["gen"],                253.6,  0.15),
    ("上网电量",            ec["gen_net"],            206.2,  0.15),
    ("上网发电收入",        ec["power_rev"],          80.63,  0.05),
    ("炉渣收入",            ec["slag_rev"],           16.5,   0.05),
    ("变动成本合计",        ec["var_cost"],           99.4,   0.10),
    ("边际贡献(含处置费)",  ec["margin"],             77.72,  0.05),
    ("边际贡献(不含处置费)", ec["margin_ex_disposal"], -2.28,  0.05),
    ("盈亏平衡处置价",      ec["be_price"],           2.28,   0.05),
    ("吨固定成本",          FIXED_PT,                 47.35,  0.02),
    ("全成本净利",          ec["full_cost_profit"],   30.38,  0.05),
    ("临界入炉热值(V4口径)", ec["q_crit_fuel"],        1022.8, 0.20),
    ("临界检测热值(V4口径)", ec["q_crit_fuel_lab"],    1233.9, 0.20),
]

fails = 0
print(f"{'指标':<22}{'引擎值':>12}{'V4算例':>12}{'差':>10}{'容差':>8}  判定")
print("-" * 76)
for name, got, want, tol in CHECKS:
    if got is None:
        print(f"{name:<22}{'None':>12}{want:>12}{'—':>10}{tol:>8}  ❌"); fails += 1
        continue
    d = got - want
    ok = abs(d) <= tol
    fails += 0 if ok else 1
    print(f"{name:<22}{got:>12.3f}{want:>12.2f}{d:>+10.3f}{tol:>8.2f}  {'✅' if ok else '❌'}")

# 修正口径临界热值（引擎新增，V4 无对应值；验证与手工推导一致）
print("-" * 76)
print(f"{'临界入炉热值(含处置费)':<22}{ec['q_crit_incl']:>12.3f}{'手工≈35.81':>12}{'':>10}{'':>8}  ℹ️ 修正口径")

# 稳健性冒烟：除零保护与超上限告警
from hhv.pricing import lookup_op_params  # noqa: E402
op = lookup_op_params(25.0)
assert op["warn"], "掺烧比例超上限应产生告警"
zero = BatchInput(name="零热值", q_lab=0.0, moisture_lab=45.0, moisture_furnace=50.0,
                  ash=35.0, stock_days=5.0, blend_pct=20.0, disposal_price=80.0,
                  leachate_rate=0.35)
ec0 = batch_economics(zero, fixed_per_t=FIXED_PT, v4_compat=True)
assert ec0["q_crit_fuel"] is None, "发电收入为 0 时临界热值应为 None 而非除零崩溃"
print("稳健性冒烟（超上限告警 / 除零保护）✅")

print("=" * 76)
if fails:
    print(f"验证失败 {fails} 项——引擎与 V4 语义不一致，禁止用于定价！")
    sys.exit(1)
print("全部 13 项验证通过：引擎与同事 V4 Excel 语义逐格一致。")
