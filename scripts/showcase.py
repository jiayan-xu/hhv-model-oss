# -*- coding: utf-8 -*-
"""日均值可展示路径：反推 → 闸门 → 综合固废定价草图 → 单页 HTML。

  python scripts/showcase.py
  python scripts/showcase.py --reuse     # 已有 result.json 则跳过 25s 回归

不拉 SNMIS。二期时序由 run.py 按 config 接 DCS 小时库（流量形状 + 温压氧）。
输出：outputs/config.snmis_daily/showcase.html
      outputs/pricing/市场化垃圾定价建议_V4.1.xlsx（厂内炉渣口径）
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hhv.pricing import (BatchInput, batch_economics, fixed_cost_per_t,  # noqa: E402
                         load_plant_slag, price_tier_table)

KJ_PER_KCAL = 4.186
# 演示用；真实项目请换成 config.example.yaml 映射后的本机 config.yaml
CONFIG = ROOT / "config.demo.yaml"
OUT = ROOT / "outputs" / "config.demo"
HTML = OUT / "showcase.html"
PRICES = [40, 50, 60, 70, 80, 90, 100]


def _kj_to_kcal(x: float) -> float:
    return float(x) / KJ_PER_KCAL


def _pick_q(ci: dict, kind: str) -> dict | None:
    if not ci:
        return None
    if kind == "msw":
        return ci.get("Q_生活")
    for k, v in ci.items():
        if k.startswith("Q_") and k != "Q_生活" and isinstance(v, dict):
            return v
    return None


def _all_isw(ci: dict) -> dict:
    if not ci:
        return {}
    return {k: v for k, v in ci.items()
            if k.startswith("Q_") and k != "Q_生活" and isinstance(v, dict)}


def _ci_sd(d: dict) -> float:
    """95% CI → 1σ。"""
    return (float(d["hi"]) - float(d["lo"])) / (2 * 1.959964)


def _polyline(xs: list[float], ys: list[float], w: float, h: float, pad=8.0) -> str:
    finite = [(x, y) for x, y in zip(xs, ys) if y == y]
    if not finite:
        return ""
    y0 = min(p[1] for p in finite)
    y1 = max(p[1] for p in finite)
    span = (y1 - y0) or 1.0
    n = max(len(xs) - 1, 1)
    pts = []
    for i, y in enumerate(ys):
        if y != y:
            continue
        px = pad + (w - 2 * pad) * i / n
        py = h - pad - (h - 2 * pad) * (y - y0) / span
        pts.append(f"{px:.1f},{py:.1f}")
    return " ".join(pts)


def _bars(values: list[float], w: float, h: float) -> str:
    if not values:
        return ""
    n = len(values)
    mx = max(values) or 1.0
    gap = 4.0
    bw = (w - gap * (n + 1)) / n
    parts = []
    for i, v in enumerate(values):
        bh = (h - 16) * (v / mx)
        x = gap + i * (bw + gap)
        y = h - 12 - bh
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{bh:.1f}"/>'
        )
    return "\n".join(parts)


def run_model(reuse: bool) -> dict:
    js = OUT / "result.json"
    if reuse and js.exists():
        return json.loads(js.read_text(encoding="utf-8"))
    sys.path.insert(0, str(ROOT))
    import run as runmod
    rc = runmod.main(str(CONFIG))
    if rc != 0:
        raise RuntimeError(f"run.py 退出码 {rc}")
    return json.loads(js.read_text(encoding="utf-8"))


def pricing_block(res: dict) -> dict:
    plant = load_plant_slag()
    if plant is None:
        raise RuntimeError("缺少 data/plant/slag_outbound.csv")
    slag = plant["rate_pct"]
    ci_b = res.get("ci_anchored") or {}
    ci_c = res.get("ci_furnace") or {}
    q_isw_b = _pick_q(ci_b, "isw")
    q_msw_b = _pick_q(ci_b, "msw")
    q_isw_c = _pick_q(ci_c, "isw")
    if not q_isw_b or not q_msw_b:
        raise RuntimeError("result.json 缺解法B热值")
    q_b = _kj_to_kcal(q_isw_b["mean"])
    q_c = _kj_to_kcal(q_isw_c["mean"]) if q_isw_c else q_b
    sd_b = _kj_to_kcal(_ci_sd(q_isw_b))
    days = 330.0
    total_burn = 2100.0 + 2300.0 * 0.20
    fixed_pt = fixed_cost_per_t(total_burn, days)
    batches = []
    for name, q, sd in (
        ("解法B 综合固废", q_b, sd_b),
        ("解法C 综合固废", q_c, sd_b),
    ):
        b = BatchInput(
            name, q, 30.0, 30.0, 25.0, 5.0, 20.0, 80.0,
            leachate_rate=0.15, daily_t=380.0,
            notes="含水/灰分演示值，非分来源化验",
        )
        e = batch_economics(b, fixed_per_t=fixed_pt, slag_rate_pct=slag)
        tiers = price_tier_table(q, max(sd, 1.0), b, PRICES, slag_rate_pct=slag)
        batches.append({"name": name, "q_kcal": q, "q_sd": sd, "e": e, "tiers": tiers})
    # V4 造纸对照（化验算例 1200）
    paper = BatchInput("V4造纸算例", 1200, 45, 50, 35, 5, 20, 80,
                       leachate_rate=0.35, daily_t=150)
    pe = batch_economics(paper, fixed_per_t=fixed_pt, slag_rate_pct=slag)
    return {
        "slag": plant,
        "msw_b_kcal": _kj_to_kcal(q_msw_b["mean"]),
        "batches": batches,
        "paper": {"e": pe, "q": 1200.0},
    }


def render_html(res: dict, weekly, price: dict) -> str:
    g = res["gate"]
    gp = res.get("gate_plant") or {}
    s1, s2 = res["stats1"], res["stats2"]
    weeks = [str(w) for w in weekly["week"].tolist()]
    q1 = [float(x) for x in weekly["qv_p1"].tolist()]
    q2 = [float(x) for x in weekly["qv_p2"].tolist()]
    n = len(weeks)
    xs = list(range(n))
    p1 = _polyline(xs, q1, 640, 160)
    p2 = _polyline(xs, q2, 640, 160)
    kit = res.get("kitchen_monthly") or {}
    kit_months = sorted(kit, key=lambda x: int(x))
    kit_vals = [kit[m] for m in kit_months]
    kit_svg = _bars(kit_vals, 420, 88)
    ci_b = res["ci_anchored"]
    ci_c = res.get("ci_furnace") or {}
    ci_a = res.get("ci_free") or {}
    qb_m, qb_i = _pick_q(ci_b, "msw"), _pick_q(ci_b, "isw")
    qc_m, qc_i = _pick_q(ci_c, "msw"), _pick_q(ci_c, "isw")
    qa_m, qa_i = _pick_q(ci_a, "msw"), _pick_q(ci_a, "isw")
    isw_lines = ""
    for k, d in _all_isw(ci_b).items():
        dc = _all_isw(ci_c).get(k) or {}
        isw_lines += f"{k.replace('Q_','')} B {d['mean']:.0f}（{d['lo']:.0f}–{d['hi']:.0f}） C {dc.get('mean', float('nan')):.0f}<br/>"

    def fmt_q(d):
        if not d:
            return "—"
        return f"{d['mean']:.0f}"

    def fmt_ci(d):
        if not d:
            return ""
        return f"{d['lo']:.0f}–{d['hi']:.0f}"

    gate_rows = ""
    for r in (g.get("detail") or []):
        gate_rows += (
            f"<tr><td>{int(r['month']):02d}</td>"
            f"<td class='num'>{r['qv_hist']:.0f}</td>"
            f"<td class='num'>{r['qv_now']:.0f}</td>"
            f"<td class='num'>{100 * r['delta']:.1f}%</td>"
            f"<td class='num'>{r['k_month']:.3f}</td></tr>\n"
        )
    plant_rows = ""
    for r in (gp.get("detail") or []):
        plant_rows += (
            f"<tr><td>{int(r['month']):02d}</td>"
            f"<td class='num'>{r['qv_hist']:.0f}</td>"
            f"<td class='num'>{r['qv_now']:.0f}</td>"
            f"<td class='num'>{100 * r['delta']:.1f}%</td></tr>\n"
        )
    kit_labels = " ".join(
        f"<span>{int(m)}月 {100 * kit[m]:.0f}%</span>" for m in kit_months
    )
    pb, pc = price["batches"][0], price["batches"][1]
    paper = price["paper"]
    slag = price["slag"]

    def tier_rows(tiers):
        html = ""
        for t in tiers:
            flag = "高风险" if t["loss_prob"] > 0.2 else (
                "关注" if t["loss_prob"] > 0.05 else "安全")
            cls = "bad" if t["loss_prob"] > 0.2 else ("ok" if t["loss_prob"] <= 0.05 else "")
            html += (
                f"<tr><td class='num'>{t['price']:.0f}</td>"
                f"<td class='num'>{t['margin_mean']:.1f}</td>"
                f"<td class='num'>{t['margin_p5']:.1f}</td>"
                f"<td class='num {cls}'>{100 * t['loss_prob']:.1f}% {flag}</td></tr>\n"
            )
        return html

    dm = g.get("delta_mean")
    dm_s = f"{100 * dm:.1f}%" if dm is not None else "—"
    pdm = gp.get("delta_mean")
    pdm_s = f"{100 * pdm:.1f}%" if pdm is not None else "—"

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>掺烧热值反推 · 日均值路径</title>
<style>
@import url("https://fonts.googleapis.com/css2?family=Archivo+Black&family=IBM+Plex+Mono:wght@400;500&family=Newsreader:opsz,wght@6..72,500&display=swap");
:root {{
  --paper: #e8e4dc;
  --ink: #141414;
  --mute: #5c5a54;
  --line: #141414;
  --accent: #c45c26;
  --ok: #2f6b3a;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
html, body {{ background: var(--paper); color: var(--ink); }}
body {{
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 13px;
  line-height: 1.45;
  min-height: 100dvh;
}}
.wrap {{ max-width: 1180px; margin: 0 auto; padding: 28px 32px 64px; }}
header {{
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 24px;
  border-bottom: 3px solid var(--ink);
  padding-bottom: 18px;
  margin-bottom: 28px;
}}
.kicker {{
  letter-spacing: 0.18em;
  font-size: 11px;
  text-transform: uppercase;
  color: var(--mute);
}}
h1 {{
  font-family: "Archivo Black", "Arial Black", sans-serif;
  font-size: clamp(32px, 5vw, 56px);
  letter-spacing: -0.04em;
  line-height: 0.92;
  text-transform: uppercase;
  margin-top: 8px;
}}
.stamp {{
  text-align: right;
  align-self: end;
  font-size: 11px;
  letter-spacing: 0.08em;
}}
.stamp b {{ color: var(--accent); font-weight: 500; }}
.hero {{
  display: grid;
  grid-template-columns: 1.4fr 1fr;
  gap: 0;
  border: 1px solid var(--ink);
  margin-bottom: 28px;
}}
.hero > div {{ padding: 22px 24px; }}
.hero > div + div {{ border-left: 1px solid var(--ink); }}
.big {{
  font-family: "Archivo Black", "Arial Black", sans-serif;
  font-size: clamp(48px, 7vw, 84px);
  letter-spacing: -0.05em;
  line-height: 0.85;
}}
.big small {{ font-size: 0.28em; letter-spacing: 0.12em; display: block; margin-top: 10px; color: var(--mute); }}
.meta {{ margin-top: 16px; color: var(--mute); max-width: 52ch; }}
.grid4 {{
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  border: 1px solid var(--ink);
  margin-bottom: 28px;
}}
.grid4 > div {{
  padding: 16px 18px 18px;
  border-right: 1px solid var(--ink);
}}
.grid4 > div:last-child {{ border-right: 0; }}
.lbl {{ font-size: 10px; letter-spacing: 0.16em; text-transform: uppercase; color: var(--mute); margin-bottom: 8px; }}
.val {{ font-family: "Archivo Black", sans-serif; font-size: 28px; letter-spacing: -0.03em; }}
.val em {{ font-style: normal; color: var(--accent); }}
.sub {{ font-size: 11px; color: var(--mute); margin-top: 4px; }}
.split {{
  display: grid;
  grid-template-columns: 1.15fr 0.85fr;
  gap: 28px;
  margin-bottom: 28px;
}}
h2 {{
  font-family: "Archivo Black", "Arial Black", sans-serif;
  font-size: 14px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  border-bottom: 1px solid var(--ink);
  padding-bottom: 8px;
  margin-bottom: 14px;
}}
table {{ width: 100%; border-collapse: collapse; }}
th, td {{
  text-align: left;
  padding: 6px 8px;
  border-bottom: 1px solid #cfc9bc;
  font-variant-numeric: tabular-nums;
}}
th {{ font-size: 10px; letter-spacing: 0.12em; text-transform: uppercase; color: var(--mute); }}
.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
.ok {{ color: var(--ok); }}
.bad {{ color: var(--accent); font-weight: 500; }}
svg.chart {{ width: 100%; height: auto; display: block; background: #efebe3; border: 1px solid var(--ink); }}
.legend {{ display: flex; gap: 18px; font-size: 11px; margin: 8px 0 0; color: var(--mute); }}
.sw {{ display: inline-block; width: 18px; height: 3px; margin-right: 6px; vertical-align: middle; }}
.kit-row {{ display: flex; flex-wrap: wrap; gap: 10px 16px; font-size: 11px; margin-top: 8px; color: var(--mute); }}
.note {{
  border-top: 3px solid var(--ink);
  margin-top: 36px;
  padding-top: 16px;
  color: var(--mute);
  font-size: 12px;
  max-width: 78ch;
}}
.note strong {{ color: var(--ink); font-weight: 500; }}
@media (max-width: 900px) {{
  .hero, .split, .grid4, header {{ grid-template-columns: 1fr; }}
  .hero > div + div {{ border-left: 0; border-top: 1px solid var(--ink); }}
  .grid4 > div {{ border-right: 0; border-bottom: 1px solid var(--ink); }}
}}
</style>
</head>
<body>
<div class="wrap">
<header>
  <div>
    <div class="kicker">固废智能运营台 / hhv-model / SNMIS daily</div>
    <h1>日均值<br/>反推路径</h1>
  </div>
  <div class="stamp">
    {datetime.now():%Y-%m-%d %H:%M}<br/>
    闸门 <b>{g['status']}</b> · k {g['k']:.4f}<br/>
    合格周 {res['n_weeks']} · 主推 {res['primary_label']}
  </div>
</header>

<section class="hero">
  <div>
    <div class="kicker">解法 B / C 工业固废（禁止单锚）</div>
    <div class="big">{fmt_q(qb_i)}<small>kJ/kg · dashboard 过滤口径 · 大件进生活 · {isw_lines}</small></div>
    <p class="meta">蒸汽反平衡闸门 {g['status']}（δ={dm_s}）。厂内热值平行闸门 {gp.get('status','—')}（δ={pdm_s}，k={float(gp.get('k') or 0):.3f}）。后者不进回归，只对照。</p>
  </div>
  <div>
    <div class="lbl">生活垃圾锚（B）</div>
    <div class="val">{fmt_q(qb_m)} <em>kJ/kg</em></div>
    <div class="sub">C {fmt_q(qc_m)} · A 自由回归 {fmt_q(qa_m)} / 固废 {fmt_q(qa_i)}</div>
    <div class="lbl" style="margin-top:18px">折 kcal（定价引擎）</div>
    <div class="val">{price['msw_b_kcal']:.0f} / {pb['q_kcal']:.0f}</div>
    <div class="sub">生活 / 综合固废 · ÷ {KJ_PER_KCAL}</div>
  </div>
</section>

<div class="grid4">
  <div>
    <div class="lbl">一期有效日</div>
    <div class="val">{s1['valid_days']}/{s1['total_days']}</div>
    <div class="sub">剔除 {s1['drop_rate_pct']}%</div>
  </div>
  <div>
    <div class="lbl">二期有效日</div>
    <div class="val">{s2['valid_days']}/{s2['total_days']}</div>
    <div class="sub">η {res.get('eff2',{}).get('eta_typ',0):.3f}</div>
  </div>
  <div>
    <div class="lbl">掺烧闭合</div>
    <div class="val">{100 * (res.get('balance_rel_mean') or 0):.1f}%</div>
    <div class="sub">库存口径 {100 * (res.get('stock_rel_mean') or 0):.1f}%</div>
  </div>
  <div>
    <div class="lbl">炉渣出厂/入炉</div>
    <div class="val">{slag['rate_pct']:.1f}%</div>
    <div class="sub">{slag['n_rows']} 个月 · {slag['slag_out_t']:.0f}/{slag['furnace_input_t']:.0f} t</div>
  </div>
</div>

<div class="split">
  <div>
    <h2>周序列 · 一期锚 vs 二期混烧</h2>
    <svg class="chart" viewBox="0 0 640 160" role="img" aria-label="周热值">
      <polyline fill="none" stroke="#141414" stroke-width="2" points="{p1}"/>
      <polyline fill="none" stroke="#c45c26" stroke-width="2" points="{p2}"/>
    </svg>
    <div class="legend">
      <span><i class="sw" style="background:#141414"></i>一期 Q生活</span>
      <span><i class="sw" style="background:#c45c26"></i>二期 Q混</span>
    </div>
  </div>
  <div>
    <h2>一期餐厨 / 入厂</h2>
    <svg class="chart" viewBox="0 0 420 88" aria-label="餐厨占比">{kit_svg}</svg>
    <div class="kit-row">{kit_labels}</div>
  </div>
</div>

<div class="split">
  <div>
    <h2>蒸汽反平衡闸门</h2>
    <table>
      <thead><tr><th>月</th><th class="num">历史Q</th><th class="num">当期Q</th><th class="num">差</th><th class="num">k</th></tr></thead>
      <tbody>{gate_rows}</tbody>
    </table>
  </div>
  <div>
    <h2>厂内热值平行闸门</h2>
    <table>
      <thead><tr><th>月</th><th class="num">二期厂内</th><th class="num">一期厂内</th><th class="num">差</th></tr></thead>
      <tbody>{plant_rows}</tbody>
    </table>
  </div>
</div>

<h2>定价草图 · 综合固废（反推 Q，处置费演示 80 元/t）</h2>
<div class="grid4" style="margin-top:0">
  <div>
    <div class="lbl">{pb['name']}</div>
    <div class="val">{pb['q_kcal']:.0f}</div>
    <div class="sub">kcal/kg · σ {pb['q_sd']:.0f} · 边际 {pb['e']['margin']:.1f} 元/t</div>
  </div>
  <div>
    <div class="lbl">{pc['name']}</div>
    <div class="val">{pc['q_kcal']:.0f}</div>
    <div class="sub">kcal/kg · 边际 {pc['e']['margin']:.1f} 元/t</div>
  </div>
  <div>
    <div class="lbl">盈亏平衡处置价 B</div>
    <div class="val">{pb['e']['be_price']:.0f}</div>
    <div class="sub">元/t · 负值表示发电+炉渣已覆盖变动成本</div>
  </div>
  <div>
    <div class="lbl">V4 造纸算例对照</div>
    <div class="val">{paper['q']:.0f}</div>
    <div class="sub">kcal · 边际 {paper['e']['margin']:.1f} 元/t</div>
  </div>
</div>
<p class="sub" style="margin:10px 0 16px">含水 30% / 灰分 25% 为综合固废演示参数，不是分来源化验。报价分档用解法 B 的 CI 当 σ。</p>
<table>
  <thead><tr><th>处置价 元/t</th><th class="num">期望边际</th><th class="num">P5 边际</th><th class="num">亏损概率</th></tr></thead>
  <tbody>{tier_rows(pb['tiers'])}</tbody>
</table>

<p class="note">
  <strong>这是可展示的日均值路径。</strong>
  数据：生产日报一蒸汽 + 指标日报逐日温压氧排烟 + dashboard 过滤口径固废 + 地磅生活/大件 + 日报炉渣。
  验证链：<a href="verify_isw.html">verify_isw.html</a>（<code>python scripts/verify_isw_daily.py</code>）。
  固废用 dashboard 过滤口径（货名含「其他」+ 跳过环卫所/城东 + 白名单）。大件粉碎与生活源/厨余/机扫一律算生活垃圾。
  未做：DCS 小时曲线、减温水/排污连续表、分来源回归（建装/农林/造纸拆 α）。
  8 月蒸汽差 19% 是一期餐厨冲到 59%，不是填报空值；厂内 Q 闸门已过。
  定价禁止单锚：B 与 C 并报。精细化要分来源进厂明细或小时 SIS 时再开下一刀。
</p>
</div>
</body>
</html>
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reuse", action="store_true", help="已有 result.json 则不重跑回归")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    print("== 1/3 反推 ==")
    res = run_model(args.reuse)
    import pandas as pd
    weekly = pd.read_csv(OUT / "weekly_series.csv")

    print("== 2/3 定价草图 + V4.1 xlsx ==")
    price = pricing_block(res)
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_pricing_report.py")],
        cwd=str(ROOT), check=False,
    )

    print("== 3/3 HTML ==")
    HTML.write_text(render_html(res, weekly, price), encoding="utf-8")
    print("showcase:", HTML)
    print(f"闸门 {res['gate']['status']} k={res['gate']['k']:.4f} 周={res['n_weeks']}")
    pb = price["batches"][0]
    print(f"综合固废 B {pb['q_kcal']:.0f} kcal/kg  边际 {pb['e']['margin']:.1f} 元/t  "
          f"盈亏平衡价 {pb['e']['be_price']:.1f} 元/t")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
