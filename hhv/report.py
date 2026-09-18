"""结果报告生成：Markdown 汇总。"""
from __future__ import annotations

from datetime import datetime


def _fmt_ci(d: dict) -> str:
    return f"{d['mean']:.0f} kJ/kg（95%CI {d['lo']:.0f} ~ {d['hi']:.0f}）"


def _emit_ci(L: list, ci: dict | None) -> None:
    if not ci:
        L.append("- （无）")
        return
    for nm, d in ci.items():
        if nm.startswith("_"):
            continue
        L.append(f"- {nm}：{_fmt_ci(d)}")


def render_report(cfg: dict, res: dict) -> str:
    L: list[str] = []
    L.append("# 掺烧工业固废热值反推报告")
    L.append("")
    L.append(f"- 生成时间：{datetime.now():%Y-%m-%d %H:%M}")
    L.append(f"- 项目：{cfg['project']}")
    L.append(f"- 效率模式：{res['eff1'].get('mode')}（一期典型 η={res['eff1'].get('eta_typ', 0):.3f}，"
             f"二期典型 η={res['eff2'].get('eta_typ', 0):.3f}）")
    L.append(f"- 主报口径：{res.get('primary_label', '解法B')}")
    if res.get("primary_note"):
        L.append(f"- 口径说明：{res['primary_note']}")
    L.append(f"- k 因子：{res.get('anchor_k', 1):.4f}")
    if res.get("balance_rel_mean") is not None:
        flag = " ⚠️" if res["balance_rel_mean"] > 0.08 else ""
        L.append(f"- 掺烧闭合 |入炉−滞后生活−固废|/入炉 周均：{res['balance_rel_mean']:.1%}{flag}")
    if res.get("stock_rel_mean") is not None:
        L.append(f"- 库存口径 |入炉−滞后入厂|/入炉 周均：{res['stock_rel_mean']:.1%}")
    if not res['eff2'].get('has_o2', True):
        L.append("- ⚠️ 未检测到有效 O2/排烟温度列，效率采用典型常数退化模式，CI 相应放宽")
    if res.get("dcs2"):
        m = res["dcs2"]
        L.append(f"- 二期时序：DCS 小时库（{m.get('n_hours')} 小时，"
                 f"{m.get('window', ['?', '?'])[0]} ~ {m.get('window', ['?', '?'])[1]}；"
                 f"f4 形状覆盖 {m.get('frac_shaped')}；"
                 f"运行炉均 {m.get('mean_furnaces_on')}）")
        for note in m.get("notes") or []:
            L.append(f"  - {note}")
    L.append("")
    L.append("## 一、数据筛选统计")
    L.append("")
    L.append("| 炉 | 日历日 | 有效日 | 剔除率 | 有效周 |")
    L.append("|---|---|---|---|---|")
    for tag, st in (("一期", res["stats1"]), ("二期", res["stats2"])):
        L.append(f"| {tag} | {st['total_days']} | {st['valid_days']} | "
                 f"{st['drop_rate_pct']}% | {st['valid_weeks']} |")
    L.append("")
    L.append("## 二、交叉验证闸门（k 因子）")
    L.append("")
    L.append(f"**判定：{res['gate']['status']}** — {res['gate']['msg']}")
    if res["gate"].get("detail") is not None:
        L.append("")
        L.append("| 月份 | 历史Q(二期两年前) | 当期Q(一期) | 差值 | 月度k |")
        L.append("|---|---|---|---|---|")
        for _, r in res["gate"]["detail"].iterrows():
            km = r["k_month"] if "k_month" in r else float("nan")
            L.append(f"| {int(r['month'])} 月 | {r['qv_hist']:.0f} | {r['qv_now']:.0f} | "
                     f"{r['delta']:.1%} | {km:.3f} |")
    gp = res.get("gate_plant")
    if gp and gp.get("detail") is not None:
        L.append("")
        L.append(f"**厂内热值平行闸门：{gp['status']}** — {gp['msg']}")
        L.append("")
        L.append("| 月份 | 历史厂内Q(二期) | 当期厂内Q(一期) | 差值 | 月度k |")
        L.append("|---|---|---|---|---|")
        for _, r in gp["detail"].iterrows():
            km = r["k_month"] if "k_month" in r else float("nan")
            L.append(f"| {int(r['month'])} 月 | {r['qv_hist']:.0f} | {r['qv_now']:.0f} | "
                     f"{r['delta']:.1%} | {km:.3f} |")
    ge = res.get("gate_eta")
    if ge:
        L.append("")
        L.append(f"**效率互证闸门：{ge['status']}** — {ge['msg']}")
        if ge.get("n_days", 0) >= 10:
            L.append("")
            L.append(f"| 指标 | 值 |")
            L.append(f"|---|---|")
            L.append(f"| DCS 隐含 η 均值 | {ge['eta_dcs_mean']:.3f} |")
            L.append(f"| DCS 隐含 η P5~P95 | {ge['eta_dcs_p05']:.3f} ~ {ge['eta_dcs_p95']:.3f} |")
            L.append(f"| 反平衡 η 均值 | {ge['eta_rb_mean']:.3f} |")
            L.append(f"| 互证天数 | {ge['n_days']} |")
    L.append("")
    L.append("## 三、热值反推结果（kJ/kg，收到基低位）")
    L.append("")
    L.append("### 解法A：自由回归（校验）")
    _emit_ci(L, res["ci_free"])
    L.append("")
    L.append(f"### 解法B：一期同周 × k（锚均值 = {res['anchor']:.0f} kJ/kg，k={res.get('anchor_k', 1):.4f}）")
    _emit_ci(L, res["ci_anchored"])
    L.append("")
    L.append("### 解法C：二期同炉历史周对齐")
    _emit_ci(L, res.get("ci_furnace"))
    L.append("")
    resid = res["fit_free"]["resid"]
    L.append(f"- 回归残差（自由解）：均值 {resid.mean():.0f}，标准差 {resid.std():.0f} kJ/kg"
             f"（样本周数 {len(resid)}，Bootstrap {res['ci_free']['_n_boot']} 次）")
    L.append("")
    L.append("## 四、使用说明")
    L.append("")
    L.append("- 解法B：当期同周一期热值 × k，吃掉生活垃圾季节项，并把一期刻度换到二期表计。")
    L.append("- 解法C：二期历史纯烧按 ISO 周序号对齐，同炉自比；年份间垃圾成分变化时会偏（餐厨档已加权）。")
    L.append("- PASS：主报解法B；DUAL：B 与 C 并报；若厂内平行更优，**对外主参考厂内 Q**；ALERT：禁止单锚定价。")
    L.append("- 解法A 与主报分来源热值相差超过 ±500 kJ/kg 时，先查台账 α 与物料闭合残差。")
    L.append("- 最终定价前按来源抽样氧弹量热化验比对。")
    L.append("")
    L.append("## 五、8 月差与物料残差（诊断，不改回归周）")
    L.append("")
    L.append("- 8 月蒸汽反平衡差大，主要是一期餐厨占比冲高（入厂约六成）把吨汽/热值打低，")
    L.append("  加上 2024 二期同月吨汽偏高；生产日报日列无空值，不是填报缺失。")
    L.append("- 厂内「垃圾热值」平行闸门吃的是 SNMIS 自己的 Q，不受反平衡放大；与蒸汽闸门对照看。")
    L.append("- 掺烧闭合偏高：入厂系统大于入炉（坑存/计量），叠加二期减负荷周（如 W25–W28、W34）。")
    L.append("  不是地磅固废从「已含固废的二期入厂」里双减——不减 ISW 日残差更大。高 α 周仍留在回归里。")
    kit = res.get("kitchen_monthly") or {}
    if kit:
        L.append("")
        L.append("| 月份 | 一期餐厨/入厂 |")
        L.append("|---|---|")
        for m in sorted(kit, key=lambda x: int(x)):
            L.append(f"| {int(m)} 月 | {float(kit[m]):.1%} |")
    L.append("")
    return "\n".join(L)
