"""市场化垃圾定价引擎 V4.1 —— 同事 Excel 测算模型 V4 的 Python 引擎化。

公式语义与 V4 逐格对齐（验证基准：V4 文件 12-算例结果速览 静态值），
并在吸收时修正 V4 的三处口径断点（见 README「引擎与 V4 差异」）：
  P1-1  组合/年度聚合一律使用实际加权日处置量（V4 的 05 表手填 100 吨/日已废弃）
  P1-2  临界热值双口径并列：燃料口径（=V4 原公式）与含处置费口径（定价建议用）
  P1-3  掺烧整体测算使用实际组合量，上限值只做校验
厂内口径（默认，v4_compat=False）：
  炉渣率 = 实际出厂炉渣 t / 同期入炉 t（混烧无法按灰分分摊到批次）
  含水率只取化验均值，k_moist=1（检测 LHV 已是该含水收到基，无入炉另测）
V4 逐格验证请传 v4_compat=True。
稳健性：所有除法带保护；掺烧比例超过上限显式告警并按上限档取参。
"""
from __future__ import annotations

import csv
import math
import pathlib
from dataclasses import dataclass, field

import numpy as np

_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_SLAG_CSV = _ROOT / "data" / "plant" / "slag_outbound.csv"

KJ_PER_KCAL = 4.186

# ── 02-运行参数库（按掺烧比例分档，来源 V4）──────────────────────────
# (掺烧比例%, 锅炉效率%, 厂用电率%, 汽耗率kg/kwh, 进汽焓, 给水焓, SCR%,
#  石灰kg/t, 活性炭kg/t, 氨水kg/t, 天然气m³/t)
OP_TABLE = [
    (0, 81.0, 13.0, 4.8, 3214.0, 546.0, 6.0, 14.5, 0.62, 1.28, 0.25),
    (5, 81.0, 13.1, 4.8, 3214.0, 546.0, 6.0, 14.8, 0.64, 1.29, 0.25),
    (10, 80.0, 13.2, 4.8, 3214.0, 546.0, 6.0, 15.2, 0.65, 1.30, 0.26),
    (15, 79.0, 13.4, 4.8, 3214.0, 546.0, 6.0, 15.6, 0.67, 1.33, 0.27),
    (20, 78.0, 13.5, 4.8, 3214.0, 546.0, 6.0, 16.1, 0.68, 1.35, 0.28),
]
BLEND_CAP = 20.0  # V4 口径：掺烧比例上限 20%

# ── 03-成本要素库默认值（来源 V4）──────────────────────────────────
COST = {
    "grid": 0.391,        # 上网电价 元/kwh
    "slag_price": 30.0,   # 炉渣外售 元/t
    "lime": 475.11,       # 石灰 元/t
    "ac": 2660.0,         # 活性炭 元/t
    "ammonia": 833.0,     # 氨水 元/t
    "ngas": 4.19,         # 天然气 元/m³
    "dh2o": 7.93,         # 除盐水 元/t
    "water_per_t": 0.10,  # 吨垃圾耗水 t/t
    "leachate": 42.5,     # 渗滤液处置 元/t
    "flyash": 40.0,       # 飞灰基准 元/t
    "maint": 16.0,        # 检修人工基准 元/t
    "wear_add": 1.30,     # 高灰分检修加成
    "flyash_add": 1.15,   # 高灰分飞灰加成
}
# 13-停炉经济性 默认配置（六炉四机）
SHUTDOWN = {
    "design_tpd": 2700.0, "native_intake": 2300.0, "native_burn": 2100.0,
    "blend_cap": 0.20, "days": 330.0,
    "depr": 1200.0, "labor": 800.0, "fin": 1500.0, "om": 500.0,  # 万元/年
    "recoverable": 0.30,
    "stops": [  # (名称, 停炉产能t/d, 单次启炉万, 年次数, 单次保养万, 台数, 寿命损耗万/次)
        ("停1台600炉", 600.0, 10.0, 1, 3.0, 1, 2.0),
        ("停2台300炉", 600.0, 6.0, 2, 3.0, 2, 2.0),
    ],
}

# ── 生活垃圾实测锚点（2025 年厂内自检，湿基低位 kcal/kg）──────────────
# 仅 7/8/9 月有实测；其余月份返回实测年均值并标注 placeholder，待月度化验回填。
# 关键事实：同月二期生活垃圾系统性高于一期 +5.2%/+8.1%/+13.0%（均值约 +9%），
# 两期锚定不可混用——二期入炉生活垃圾成分应以 phase2 实测为准。
MSW_ASSAY = {
    7: (2250.0, 2367.0),   # (一期, 二期)
    8: (2061.0, 2228.0),
    9: (3363.0, 3800.0),
}


def msw_q(month: int, phase: str = "phase2") -> tuple[float, str]:
    """生活垃圾湿基低位热值 (kcal/kg)。实测月返回实测值，其余月份返回实测年均值。"""
    if month in MSW_ASSAY:
        return MSW_ASSAY[month][0 if phase == "phase1" else 1], "measured"
    vals = [v[0 if phase == "phase1" else 1] for v in MSW_ASSAY.values()]
    return sum(vals) / len(vals), "placeholder(实测年均值,待月度化验回填)"


def phase_bias() -> dict:
    """一期 vs 二期生活垃圾同月热值偏差（二期−一期）/一期。"""
    per_month = {m: (v[1] - v[0]) / v[0] for m, v in MSW_ASSAY.items()}
    vals = list(per_month.values())
    return {"per_month": per_month, "mean": sum(vals) / len(vals)}


def actual_slag_rate(slag_out_t: float, furnace_input_t: float) -> float:
    """厂内炉渣率 % = 出厂炉渣 / 同期入炉量。"""
    if furnace_input_t is None or furnace_input_t <= 0:
        raise ValueError("入炉量必须 > 0")
    if slag_out_t is None or slag_out_t < 0:
        raise ValueError("炉渣出厂量不能为负")
    return 100.0 * float(slag_out_t) / float(furnace_input_t)


def load_plant_slag(path: str | pathlib.Path | None = None) -> dict | None:
    """读 data/plant/slag_outbound.csv，按行合计出厂炉渣 / 入炉量。

    返回 {rate_pct, slag_out_t, furnace_input_t, n_rows}；无数或文件不存在则 None。
    """
    p = pathlib.Path(path) if path else DEFAULT_SLAG_CSV
    if not p.exists():
        return None
    slag = burn = 0.0
    n = 0
    with open(p, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            try:
                s = float(str(row.get("slag_out_t") or "").strip())
                b = float(str(row.get("furnace_input_t") or "").strip())
            except (TypeError, ValueError):
                continue
            if b <= 0:
                continue
            slag += s
            burn += b
            n += 1
    if n == 0 or burn <= 0:
        return None
    return {"rate_pct": actual_slag_rate(slag, burn),
            "slag_out_t": slag, "furnace_input_t": burn, "n_rows": n}


def mean_assay_value(rows: list[dict], key: str, category: str | None = None,
                     cat_field: str = "v4_category") -> float | None:
    """化验台账某列均值（含水/热值/灰分）。category 为空则全表。"""
    vals = []
    for r in rows:
        if category is not None and str(r.get(cat_field) or "") != category:
            continue
        raw = r.get(key)
        if raw is None or str(raw).strip() == "":
            continue
        try:
            vals.append(float(raw))
        except (TypeError, ValueError):
            continue
    if not vals:
        return None
    return sum(vals) / len(vals)


def feasibility_native(q_native: float, disposal: float = 100.0,
                       slag_rate_pct: float = 22.0, var_cost: float = 85.0,
                       cost: dict | None = None) -> dict:
    """原生垃圾基准吨收益。炉渣率默认 22% 仅作 V4 10 表占位，厂内请传入实际出厂口径。"""
    c = cost or COST
    op = lookup_op_params(0)
    rev_per_kcal = _power_rev_per_kcal(op, c)
    power_rev = q_native * rev_per_kcal
    slag_rev = slag_rate_pct / 100.0 * c["slag_price"]
    total = disposal + power_rev + slag_rev - var_cost
    return {"q_native": q_native, "power_rev": power_rev, "slag_rev": slag_rev,
            "disposal": disposal, "var_cost": var_cost, "native_margin": total}


def mix_heat(native_burn: float, q_native: float, msw_burn: float,
             q_market: float) -> float:
    """V4 14 表混合热值校验：混合后入炉热值 = 加权平均。"""
    return (native_burn * q_native + msw_burn * q_market) / (native_burn + msw_burn)


@dataclass
class BatchInput:
    """单批次输入（对应 V4 的 04 表绿色输入区）。"""
    name: str
    q_lab: float            # 检测低位热值 kcal/kg
    moisture_lab: float     # 检测含水率 %（厂内口径=化验均值）
    moisture_furnace: float  # 仅 v4_compat 使用；厂内口径忽略入炉另测
    ash: float              # 灰分 %
    stock_days: float       # 堆放天数
    blend_pct: float        # 掺烧比例 %
    disposal_price: float   # 处置费单价 元/t
    purchase_price: float = 0.0
    transport_price: float = 0.0
    leachate_rate: float = 0.10  # 渗滤液产生率 吨/吨
    daily_t: float = 0.0         # 日处置量 t/d
    include: bool = True         # 是否用于加权
    notes: str = ""


CALIBRATION_PATH = _ROOT / "data" / "pricing_calibration.yaml"


def load_pricing_calibration(path: str | pathlib.Path | None = None) -> dict[int, dict]:
    """加载定价效率校准（掺烧档 → {eta, aux, steam_rate}）。

    单一来源 data/pricing_calibration.yaml（897 天实测口径），供 Python 引擎与
    scripts/gen_pricing_table.py 生成的前端 OP 表共用，杜绝两套口径各自演化。
    文件不存在时返回空 dict（调用方按 V4 原始 OP_TABLE 运行）。
    """
    import yaml  # 延迟导入：未装 PyYAML 时不影响 V4 对照路径

    p = pathlib.Path(path) if path else CALIBRATION_PATH
    if not p.is_file():
        return {}
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    by_blend = raw.get("by_blend") or {}
    out: dict[int, dict] = {}
    for k, v in by_blend.items():
        if not isinstance(v, dict):
            continue
        out[int(k)] = {kk: float(vv) for kk, vv in v.items() if vv is not None}
    return out


def lookup_op_params(blend_pct: float, table: list | None = None,
                     calibration: dict[int, dict] | None = None) -> dict:
    """按掺烧比例近似匹配运行参数档（VLOOKUP TRUE 语义）。

    超上限时告警并按上限档取参（V4 会静默取档，这里显式化）。
    calibration 非空时，用实测校准值覆盖该档的 eta / aux / steam_rate，
    并在返回值里标 calibrated=True（落盘可追溯用了哪套口径）。
    """
    tbl = table or OP_TABLE
    row = tbl[0]
    for r in tbl:
        if blend_pct >= r[0]:
            row = r
    warn = None
    if blend_pct > BLEND_CAP:
        warn = f"掺烧比例 {blend_pct}% 超上限 {BLEND_CAP}%，已按上限档取参"
    keys = ["blend", "eta", "aux", "steam_rate", "h_steam", "h_fw", "scr",
            "lime_c", "ac_c", "ammonia_c", "ngas_c"]
    d = dict(zip(keys, row))
    overrides = (calibration or {}).get(int(row[0]), {})
    for k in ("eta", "aux", "steam_rate"):
        if k in overrides:
            d[k] = overrides[k]
    d["warn"] = warn
    d["calibrated"] = bool(overrides)
    return d


def batch_economics(inp: BatchInput, cost: dict | None = None,
                    fixed_per_t: float | None = None, *,
                    v4_compat: bool = False,
                    slag_rate_pct: float | None = None,
                    calibration: dict[int, dict] | None = None) -> dict:
    """单批次全链路测算（对应 V4 04 表 A19~B66 全部行）。

    v4_compat=True：含水差修正 + 灰分 50/55% 渣率（仅 validate_pricing）。
    默认厂内口径：k_moist=1，渣率必须给实际出厂/入炉。
    """
    c = cost or COST
    r: dict = {"name": inp.name, "warn": []}
    if inp.stock_days <= 0:
        k_stock = 1.0  # 已是入炉态（反推 Q），不再打堆放折
    elif inp.stock_days <= 3:
        k_stock = 0.97
    elif inp.stock_days <= 7:
        k_stock = 0.94
    else:
        k_stock = 0.92
    k_ash = 0.97 if inp.ash > 30 else 1.0
    if v4_compat:
        denom = 100.0 - inp.moisture_lab
        k_moist = (100.0 - inp.moisture_furnace) / denom if denom else 1.0
        slag_rate = 55.0 if inp.ash > 30 else 50.0
        slag_src = "v4_ash_lookup"
    else:
        k_moist = 1.0
        if slag_rate_pct is None:
            plant = load_plant_slag()
            if plant is None:
                raise ValueError(
                    "厂内口径需要实际炉渣率：填写 data/plant/slag_outbound.csv "
                    "或传入 slag_rate_pct=出厂炉渣t/入炉t×100")
            slag_rate_pct = plant["rate_pct"]
        slag_rate = float(slag_rate_pct)
        if slag_rate < 0 or slag_rate > 80:
            r["warn"].append(f"炉渣率 {slag_rate:.1f}% 超出 0~80%，请核对出厂/入炉台账")
        slag_src = "actual_outbound"
    q_in = inp.q_lab * k_stock * k_moist * k_ash
    r.update(k_stock=k_stock, k_moist=k_moist, k_ash=k_ash, q_in=q_in,
             slag_source=slag_src)
    # 三、运行参数
    op = lookup_op_params(inp.blend_pct, calibration=calibration)
    if op["warn"]:
        r["warn"].append(op["warn"])
    r.update(op)
    # 四、能量与发电（汽耗率法）
    heat_in = q_in * 1000.0
    boiler_out = op["eta"] / 100.0 * heat_in
    dh_kcal = (op["h_steam"] - op["h_fw"]) / KJ_PER_KCAL
    thr = op["steam_rate"] * dh_kcal                      # 汽机热耗 kcal/kwh
    gen = boiler_out / thr if thr else float("nan")       # 吨发电量 kwh/t
    gen_net = gen * (1 - op["scr"] / 100.0) * (1 - op["aux"] / 100.0)
    power_rev = c["grid"] * gen_net
    r.update(heat_in=heat_in, boiler_out=boiler_out, dh_kcal=dh_kcal,
             turbine_heat_rate=thr, gen=gen, gen_net=gen_net, power_rev=power_rev)
    # 五、炉渣（厂内=出厂/入炉；V4 兼容=灰分分档）
    slag_rev = slag_rate / 100.0 * c["slag_price"]
    r.update(slag_rate=slag_rate, slag_rev=slag_rev)
    # 六、变动成本
    high_ash = inp.ash > 30
    lime_c = c["lime"] * op["lime_c"] / 1000.0
    ac_c = c["ac"] * op["ac_c"] / 1000.0
    ammonia_c = c["ammonia"] * op["ammonia_c"] / 1000.0
    ngas_c = c["ngas"] * op["ngas_c"]
    maint_c = c["maint"] * (c["wear_add"] if high_ash else 1.0)
    water_c = c["dh2o"] * c["water_per_t"]
    leachate_c = inp.leachate_rate * c["leachate"]
    flyash_c = c["flyash"] * (c["flyash_add"] if high_ash else 1.0)
    var_cost = lime_c + ac_c + ammonia_c + ngas_c + maint_c + water_c + leachate_c + flyash_c
    r.update(lime_c=lime_c, ac_c=ac_c, ammonia_c=ammonia_c, ngas_c=ngas_c,
             maint_c=maint_c, water_c=water_c, leachate_c=leachate_c,
             flyash_c=flyash_c, var_cost=var_cost)
    # 七、结果
    net_income = power_rev + slag_rev + inp.disposal_price - inp.purchase_price - inp.transport_price
    margin = net_income - var_cost
    margin_ex_disposal = power_rev + slag_rev - var_cost
    be_price = var_cost - power_rev - slag_rev
    k_prod = k_stock * k_moist * k_ash
    if power_rev and power_rev > 0:
        q_crit_fuel = (var_cost - slag_rev) * q_in / power_rev          # V4 原口径（处置费=0）
        q_crit_incl = (var_cost - slag_rev - inp.disposal_price) * q_in / power_rev  # 修正口径
        r.update(q_crit_fuel=q_crit_fuel, q_crit_incl=q_crit_incl,
                 q_crit_fuel_lab=q_crit_fuel / k_prod,
                 q_crit_incl_lab=q_crit_incl / k_prod)
    else:
        r.update(q_crit_fuel=None, q_crit_incl=None,
                 q_crit_fuel_lab=None, q_crit_incl_lab=None)
    if fixed_per_t is not None:
        r["full_cost_profit"] = margin - fixed_per_t
    r.update(net_income=net_income, margin=margin,
             margin_ex_disposal=margin_ex_disposal, be_price=be_price,
             purchase_price=inp.purchase_price, transport_price=inp.transport_price,
             disposal_price=inp.disposal_price,
             fixed_per_t=fixed_per_t)
    return r


def fixed_cost_per_t(total_burn_t_day: float, days: float,
                     fixed: dict | None = None) -> float:
    """吨固定成本 = 年固定成本(万元) / 焚烧总量(万吨)。"""
    f = fixed or {k: SHUTDOWN[k] for k in ("depr", "labor", "fin", "om")}
    annual_wan = f["depr"] + f["labor"] + f["fin"] + f["om"]
    total_wan_t = total_burn_t_day * days / 10000.0
    return annual_wan / total_wan_t


def portfolio(batches: list[BatchInput], ecoms: list[dict] | None = None) -> dict:
    """组合加权（对应 V4 15 表，P1-1 修正：权重恒用实际日处置量）。"""
    ec = ecoms or [batch_economics(b) for b in batches]
    rows = [(b, e) for b, e in zip(batches, ec) if b.include]
    w_sum = sum(b.daily_t for b, _ in rows)
    if w_sum <= 0:
        raise ValueError("加权组合为空：所有批次 include=False 或日处置量为 0")

    def wavg(key: str) -> float:
        return sum(b.daily_t * e[key] for b, e in rows) / w_sum

    return {
        "total_daily_t": w_sum,
        "n_batches": len(rows),
        "margin": wavg("margin"),
        "q_in": wavg("q_in"),
        "gen": wavg("gen"),
        "power_rev": wavg("power_rev"),
        "disposal": sum(b.daily_t * b.disposal_price for b, _ in rows) / w_sum,
        "consumables": wavg("lime_c") + wavg("ac_c") + wavg("ammonia_c") + wavg("ngas_c"),
    }


def shutdown_economics(p: dict | None = None, weighted_margin: float | None = None,
                       fixed: dict | None = None) -> dict:
    """停炉经济性与协同增量（对应 V4 13 表；P1-3：实际量与上限量分离）。"""
    s = dict(SHUTDOWN)
    if p:
        s.update(p)
    market_add = s["native_intake"] * s["blend_cap"]     # 上限口径（校验用）
    total_burn = s["native_burn"] + market_add
    out = {"market_add_cap": market_add, "total_burn": total_burn,
           "days": s["days"], "fixed_per_t": fixed_cost_per_t(total_burn, s["days"], fixed)}
    # 停炉损失五分法（两方案并列，取损失小者为避免损失）
    rigid = s["depr"] + s["fin"]
    semi = s["labor"] + s["om"]
    options = []
    for name, cap, start_cost, starts, maint, units, life in s["stops"]:
        frac = cap / s["design_tpd"]
        idle = rigid * frac + semi * frac * (1 - s["recoverable"])
        loss = idle + start_cost * starts + maint * starts + s_get_standby(s, units) + life * starts
        options.append({"name": name, "frac": frac, "idle": idle,
                        "start": start_cost * starts, "maint": maint * starts,
                        "standby": s_get_standby(s, units), "life": life * starts,
                        "loss_total": loss})
    out["options"] = options
    best = min(options, key=lambda o: o["loss_total"])
    out["avoided_loss"] = best["loss_total"]
    if weighted_margin is not None:
        inc = market_add * s["days"] * weighted_margin / 10000.0  # 万元/年
        out["increment_margin"] = inc
        out["synergy_total"] = inc + out["avoided_loss"]
    return out


def s_get_standby(s: dict, units: int) -> float:
    return 5.0 * units  # 年化待机耗能 万元/台·年（V4 常数）


# ── 概率层：热值不确定度 → 边际分布与报价分档 ────────────────────────
def _power_rev_per_kcal(op: dict, cost: dict) -> float:
    """每 kcal/kg 入炉热值对应的上网发电收入 元/t（V4 链路对 Q 线性）。"""
    dh_kcal = (op["h_steam"] - op["h_fw"]) / KJ_PER_KCAL
    thr = op["steam_rate"] * dh_kcal
    gen = op["eta"] / 100.0 * 1000.0 / thr
    gen_net = gen * (1 - op["scr"] / 100.0) * (1 - op["aux"] / 100.0)
    return cost["grid"] * gen_net


def margin_distribution(q_mean: float, q_sd: float, inp: BatchInput,
                        cost: dict | None = None, n: int = 20000,
                        seed: int = 7, *, v4_compat: bool = False,
                        slag_rate_pct: float | None = None,
                        calibration: dict[int, dict] | None = None) -> dict:
    """给定热值不确定度（如 hhv 反推的 ±CI），求边际贡献分布。

    返回 MC 统计 + 解析分位数（V4 链路对 Q 严格线性，两者应一致，互为校验）。
    """
    c = cost or COST
    op = lookup_op_params(inp.blend_pct, calibration=calibration)
    e = batch_economics(inp, c, v4_compat=v4_compat, slag_rate_pct=slag_rate_pct,
                        calibration=calibration)
    rev_per_kcal = _power_rev_per_kcal(op, c)
    base = e["slag_rev"] + inp.disposal_price - inp.purchase_price - inp.transport_price - e["var_cost"]
    rng = np.random.default_rng(seed)
    q = np.maximum(rng.normal(q_mean, q_sd, n), 1.0)
    margins = base + rev_per_kcal * q
    lo, hi = float(np.percentile(margins, [5, 95]))
    out = {"q_mean": q_mean, "q_sd": q_sd, "mean": float(margins.mean()),
           "p05": lo, "p50": float(np.median(margins)), "p95": hi,
           "loss_prob": float((margins < 0).mean())}
    # 解析交叉校验（正态）
    from scipy.stats import norm
    q_be = -base / rev_per_kcal
    out["loss_prob_analytic"] = float(norm.cdf(q_be, q_mean, q_sd)) if q_sd > 0 else (
        1.0 if q_mean < q_be else 0.0)
    out["p05_analytic"] = float(q_mean - 1.6449 * q_sd) * rev_per_kcal + base
    return out


def price_tier_table(q_mean: float, q_sd: float, inp: BatchInput,
                     prices: list[float] | None = None,
                     cost: dict | None = None, *, v4_compat: bool = False,
                     slag_rate_pct: float | None = None,
                     calibration: dict[int, dict] | None = None) -> list[dict]:
    """报价分档：各处置价下的期望边际、P5 边际与亏损概率（解析精确解）。"""
    c = cost or COST
    op = lookup_op_params(inp.blend_pct, calibration=calibration)
    e = batch_economics(inp, c, v4_compat=v4_compat, slag_rate_pct=slag_rate_pct,
                        calibration=calibration)
    rev_per_kcal = _power_rev_per_kcal(op, c)
    fixed_part = e["slag_rev"] - e["purchase_price"] - inp.transport_price - e["var_cost"]
    from scipy.stats import norm
    rows = []
    for p in (prices or [40, 50, 60, 70, 80, 90, 100, 110, 120]):
        mean_m = fixed_part + p + rev_per_kcal * q_mean
        q_be = (e["var_cost"] - e["slag_rev"] - p - inp.transport_price
                + inp.purchase_price) / rev_per_kcal
        loss = float(norm.cdf(q_be, q_mean, q_sd)) if q_sd > 0 else (
            1.0 if q_mean < q_be else 0.0)
        p5_m = mean_m - 1.6449 * q_sd * rev_per_kcal
        rows.append({"price": p, "margin_mean": mean_m, "margin_p5": p5_m,
                     "loss_prob": loss})
    return rows
