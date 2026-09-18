# -*- coding: utf-8 -*-
"""日均值综合固废热值：可复跑验证链。

  python scripts/verify_isw_daily.py
  python scripts/verify_isw_daily.py --config config.snmis_daily.yaml

读：result.json / weekly_series.csv / assays_2026.csv / dashboard.db
写：outputs/<配置名>/verify_isw.{json,md,html}

三条硬闸（内部，同口径）：B vs C、α 周斜率、踢残差周。
一条对照（外部，不同口径）：VE 种类 × 化验加权。化验对不上不否决报价数。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hhv import regression  # noqa: E402

OUT = ROOT / "outputs" / "config.snmis_daily"
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _local_env import dashboard_db  # noqa: E402
DB = dashboard_db()
ASSAY = ROOT / "data" / "assays" / "assays_2026.csv"
LEDGER = ROOT / "data" / "plant" / "ledger_daily.csv"
KJ_PER_KCAL = 4.186

DATE_LO = "2026-02-01"
DATE_HI = "2026-09-01"

# 硬闸阈值（与 report.py「A 偏离 500」同级）
LIM_BC = 500.0
LIM_KICK = 200.0
LIM_RESID = 0.25
RCFG = {"bootstrap": 2000, "ci": 0.95, "huber_t": 1.345}

# 化验匹配：先 (公司关键词, 种类关键词) 精确，再按种类兜底。proxy=True 表示借同类样。
ASSAY_DIRECT = [
    ("东升", "其他", "东升·其他工业固废", False),
    ("理文", "底渣", "理文·造纸底渣", False),
    ("雷博尔", "农林", "雷博尔·农林垃圾", False),
    ("苏再投", "装修", "苏再投·装修垃圾", False),
    ("天越", "装修", "天越·装修垃圾", False),
    ("华衍", "", "华衍·沼渣", False),
    ("苏水中法", "格栅", "苏水中法·格栅垃圾", False),
    ("城西", "格栅", "苏水中法·格栅垃圾", True),
]
WASTE_FALLBACK = [
    ("装修", "装修均值（天越+苏再投）", True),
    ("农林", "雷博尔·农林垃圾", True),
    ("底渣", "理文·造纸底渣", True),
    ("格栅", "苏水中法·格栅垃圾", True),
    ("其他", "东升·其他工业固废", True),
    ("一般", "东升·其他工业固废", True),
]


def _f(x) -> float:
    return float(x) if x is not None and pd.notna(x) else float("nan")


def load_result() -> dict:
    return json.loads((OUT / "result.json").read_text(encoding="utf-8"))


def load_weekly() -> pd.DataFrame:
    df = pd.read_csv(OUT / "weekly_series.csv")
    df["week"] = df["week"].astype(str)
    return df


def load_assays() -> dict[str, float]:
    raw = pd.read_csv(ASSAY)
    out: dict[str, float] = {}
    for _, r in raw.iterrows():
        q = r.get("lhv_wet_kJ")
        if pd.isna(q):
            continue
        label = f"{r['supplier']}·{r['category']}"
        out[label] = float(q)
    # 装修两张均值，给无自家化验的装修车
    dec = [out[k] for k in out if "装修" in k]
    if dec:
        out["装修均值（天越+苏再投）"] = float(np.mean(dec))
    return out


def match_assay(company: str, waste: str, assays: dict[str, float]) -> tuple[str, float, bool]:
    c, w = str(company or ""), str(waste or "")
    for ck, wk, label, proxy in ASSAY_DIRECT:
        if ck in c and (not wk or wk in w):
            if label in assays:
                return label, assays[label], proxy
    for wk, label, proxy in WASTE_FALLBACK:
        if wk in w and label in assays:
            return label, assays[label], proxy
    return "", float("nan"), True


def ve_weighted(assays: dict[str, float]) -> dict:
    from hhv import db as _db  # noqa: E402
    con = _db.connect(DB)
    ve = pd.read_sql_query(
        "SELECT entrance_date, company_name, waste_type, "
        "COALESCE(net_weight, weight, 0) AS w FROM vehicle_entrance",
        con,
    )
    con.close()
    ve["d"] = pd.to_datetime(ve["entrance_date"], errors="coerce")
    ve = ve.dropna(subset=["d"])
    ve = ve[(ve["d"] >= DATE_LO) & (ve["d"] <= DATE_HI) & (ve["w"] > 0)]
    rows = []
    for _, r in ve.iterrows():
        label, q, proxy = match_assay(r["company_name"], r["waste_type"], assays)
        rows.append({
            "company": r["company_name"],
            "waste": r["waste_type"] or "",
            "w": float(r["w"]),
            "assay": label,
            "q": q,
            "proxy": proxy,
        })
    df = pd.DataFrame(rows)
    hit = df[df["q"].notna()]
    heat = (hit["w"] * hit["q"]).sum()
    tons = float(hit["w"].sum())
    q_mix = heat / tons if tons else float("nan")
    by_waste = (
        hit.groupby("waste", dropna=False)
        .apply(lambda g: pd.Series({
            "t": float(g["w"].sum()),
            "q": float((g["w"] * g["q"]).sum() / g["w"].sum()),
            "share": float(g["w"].sum() / tons),
        }), include_groups=False)
        .reset_index()
        .sort_values("t", ascending=False)
    )
    by_assay = (
        hit.groupby(["assay", "proxy"], dropna=False)
        .apply(lambda g: pd.Series({
            "t": float(g["w"].sum()),
            "q": float((g["w"] * g["q"]).sum() / g["w"].sum()),
        }), include_groups=False)
        .reset_index()
        .sort_values("t", ascending=False)
    )
    dec = hit[hit["waste"].astype(str).str.contains("装修", na=False)]
    rest = hit[~hit["waste"].astype(str).str.contains("装修", na=False)]
    return {
        "days": f"{DATE_LO}~{DATE_HI}",
        "tons_total": float(ve["w"].sum()),
        "tons_mapped": tons,
        "coverage": tons / float(ve["w"].sum()) if float(ve["w"].sum()) else 0.0,
        "proxy_tons": float(hit.loc[hit["proxy"], "w"].sum()),
        "q_mix": q_mix,
        "by_waste": by_waste.to_dict("records"),
        "by_assay": by_assay.to_dict("records"),
        "装修_t": float(dec["w"].sum()),
        "rest_heat": float((rest["w"] * rest["q"]).sum()),
        "rest_t": float(rest["w"].sum()),
    }


def implied_deco(assay: dict, q_model: float) -> float:
    """反推：要让化验混合等于模型 Q，装修入炉热值该是多少。"""
    t_dec = assay["装修_t"]
    if t_dec <= 0:
        return float("nan")
    need = q_model * assay["tons_mapped"] - assay["rest_heat"]
    return need / t_dec


def check_bc(res: dict) -> dict:
    qb = res["ci_anchored"]["Q_工业固废"]
    qc = res["ci_furnace"]["Q_工业固废"]
    diff = abs(qb["mean"] - qc["mean"])
    return {
        "name": "V0 双锚",
        "pass": diff < LIM_BC,
        "limit": LIM_BC,
        "diff": diff,
        "B": qb,
        "C": qc,
        "msg": f"|B−C|={diff:.0f} kJ/kg，门限 {LIM_BC:.0f}",
    }


def check_alpha(weekly: pd.DataFrame, q_msw: float, q_isw: float,
                ci_lo: float, ci_hi: float) -> dict:
    w = weekly.dropna(subset=["qv_p2", "alpha_total", "qv_p1k"]).copy()
    a = w["alpha_total"].to_numpy(float)
    y = (w["qv_p2"] - w["qv_p1k"]).to_numpy(float)
    # 餐厨把一期锚打低，spearman(α, Q混−锚)会偏正，只记不进闸
    if a.std() < 1e-9 or len(w) < 6:
        rho = float("nan")
    else:
        rho = float(pd.Series(a).corr(pd.Series(y), method="spearman"))
    w["tertile"] = pd.qcut(w["alpha_total"], 3, labels=["低α", "中α", "高α"], duplicates="drop")
    rows = []
    for name, g in w.groupby("tertile", observed=True):
        aa = float(g["alpha_total"].mean())
        qmix = float(g["qv_p2"].mean())
        pred = (1 - aa) * q_msw + aa * q_isw
        impl = (qmix - (1 - aa) * q_msw) / aa if aa > 0.01 else float("nan")
        rows.append({
            "band": str(name),
            "n": int(len(g)),
            "alpha": aa,
            "qv_p2": qmix,
            "pred": pred,
            "implied_isw": impl,
            "weeks": g["week"].tolist(),
        })
    hi = next((r for r in rows if r["band"] == "高α"), None)
    lo = next((r for r in rows if r["band"] == "低α"), None)
    impls = [r["implied_isw"] for r in rows if pd.notna(r["implied_isw"])]
    sane = bool(impls) and all(2000 < x < 12000 for x in impls)
    hi_ok = bool(hi and ci_lo <= hi["implied_isw"] <= ci_hi)
    return {
        "name": "V2 高α隐含固废",
        "pass": sane and hi_ok,
        "spearman": rho,
        "bands": rows,
        "msg": (
            f"高α隐含固废 {hi['implied_isw']:.0f}（门限 CI {ci_lo:.0f}–{ci_hi:.0f}）；"
            f"三分位 {min(impls):.0f}–{max(impls):.0f}"
            if hi and impls else "无高α组"
        ),
        "low_high": {
            "low_mix": lo["qv_p2"] if lo else None,
            "high_mix": hi["qv_p2"] if hi else None,
        },
    }


def check_kick(weekly: pd.DataFrame, q_isw: float) -> dict:
    w = weekly.dropna(subset=["qv_p2", "alpha_total", "qv_p1k"]).copy()
    keep = w[w["balance_rel"].abs() <= LIM_RESID]
    drop = w[w["balance_rel"].abs() > LIM_RESID]
    if len(keep) < 6:
        return {
            "name": "V3 踢残差周",
            "pass": False,
            "n_keep": int(len(keep)),
            "n_drop": int(len(drop)),
            "dropped": drop["week"].tolist(),
            "msg": f"留下 {len(keep)} 周 <6，无法重回归",
        }
    qmix = keep["qv_p2"]
    alphas = keep[["alpha_total"]].rename(columns={"alpha_total": "alpha::工业固废"})
    anchor = keep["qv_p1k"]
    ci = regression.bootstrap_ci(
        qmix, alphas, RCFG, anchored=True,
        q_msw_anchor=anchor, anchor_series=anchor,
    )
    q = ci["Q_工业固废"]
    move = abs(q["mean"] - q_isw)
    return {
        "name": "V3 踢残差周",
        "pass": move < LIM_KICK,
        "limit": LIM_KICK,
        "n_keep": int(len(keep)),
        "n_drop": int(len(drop)),
        "dropped": drop["week"].tolist(),
        "q": q,
        "move": move,
        "msg": f"丢掉 {len(drop)} 周（|残差|>{LIM_RESID:.0%}）后 Q={q['mean']:.0f}，移动 {move:.0f}",
    }


def check_assay(assay: dict, q_model: float, ci_lo: float, ci_hi: float) -> dict:
    q = assay["q_mix"]
    deco_impl = implied_deco(assay, q_model)
    inside = ci_lo <= q <= ci_hi
    return {
        "name": "V1 化验加权（不同口径，对照）",
        "pass": None,
        "inside_ci": bool(inside),
        "q_mix": q,
        "ci": [ci_lo, ci_hi],
        "delta_pct": (q - q_model) / q_model if q_model else float("nan"),
        "implied_装修": deco_impl,
        "basis": "化验=样品收到基低位；模型=入炉混合反平衡。装修化验远高于入炉隐含值，不作为否决。",
        "msg": (
            f"化验混合 {q:.0f} vs 模型 {q_model:.0f}（{100 * (q - q_model) / q_model:+.0f}%）；"
            f"隐含入炉装修 {deco_impl:.0f} vs 化验 1.9–2.4 万"
        ),
        **{k: assay[k] for k in (
            "days", "tons_total", "tons_mapped", "coverage", "proxy_tons",
            "by_waste", "by_assay", "装修_t", "rest_t",
        )},
    }


def verdict(v0, v2, v3, q_isw: float) -> dict:
    hard = [v0, v2, v3]
    ok = all(x["pass"] for x in hard)
    qtxt = f"{q_isw:.0f}"
    return {
        "ok": ok,
        "label": f"内部闸通过，{qtxt} 可作日均值综合数" if ok else f"内部闸未过，{qtxt} 暂停定价",
        "failed": [x["name"] for x in hard if not x["pass"]],
    }


def write_md(d: dict) -> Path:
    v = d["verdict"]
    v0, v1, v2, v3 = d["v0"], d["v1"], d["v2"], d["v3"]
    qb = v0["B"]
    lines = [
        "# 日均值综合固废热值验证",
        "",
        f"- 生成：{d['generated']}",
        f"- 复跑：`python scripts/verify_isw_daily.py`",
        f"- 模型主推：{d['q_isw']:.0f} kJ/kg（{d['q_isw_kcal']:.0f} kcal/kg），CI {d['ci_lo']:.0f}–{d['ci_hi']:.0f}",
        f"- **总判：{'通过' if v['ok'] else '未过'}** — {v['label']}",
        "",
        "## 硬闸（同口径，必须过）",
        "",
        f"| 闸 | 结果 | 说明 |",
        f"|---|---|---|",
        f"| {v0['name']} | {'PASS' if v0['pass'] else 'FAIL'} | {v0['msg']} |",
        f"| {v2['name']} | {'PASS' if v2['pass'] else 'FAIL'} | {v2['msg']} |",
        f"| {v3['name']} | {'PASS' if v3['pass'] else 'FAIL'} | {v3['msg']} |",
        "",
        "## 对照（不同口径，不否决）",
        "",
        f"{v1['msg']}",
        "",
        f"- 覆盖 {100 * v1['coverage']:.1f}% / {v1['tons_mapped']:.0f} t；借同类样 {v1['proxy_tons']:.0f} t",
        f"- {v1['basis']}",
        "",
        "| 种类 | 吨 | 化验加权 Q | 占比 |",
        "|---|---|---|---|",
    ]
    for r in v1["by_waste"]:
        lines.append(f"| {r['waste']} | {r['t']:.1f} | {r['q']:.0f} | {100 * r['share']:.1f}% |")
    lines += [
        "",
        "## α 三分位",
        "",
        "| 组 | 周数 | 均α | 二期 Q混 | 模型预测 | 隐含固废 |",
        "|---|---|---|---|---|---|",
    ]
    for r in v2["bands"]:
        lines.append(
            f"| {r['band']} | {r['n']} | {r['alpha']:.2f} | {r['qv_p2']:.0f} | "
            f"{r['pred']:.0f} | {r['implied_isw']:.0f} |"
        )
    lines += [
        "",
        "## 怎么复验",
        "",
        "1. 不改数据：`python scripts/verify_isw_daily.py`，对 `verify_isw.json`。",
        "2. 换日期或化验：改脚本顶部 `DATE_*` / `data/assays/assays_2026.csv` 再跑。",
        "3. 新进厂周：先 `python scripts/build_snmis_daily.py` + `python scripts/showcase.py`，再跑本脚本。",
        "4. DCS 小时抽查不在本链（另挂 12h 窗）。",
        "",
    ]
    p = OUT / "verify_isw.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def _rows(items, cols):
    html = ""
    for r in items:
        html += "<tr>" + "".join(f"<td class='{c[0]}'>{c[1](r)}</td>" for c in cols) + "</tr>\n"
    return html


def write_html(d: dict) -> Path:
    v = d["verdict"]
    v0, v1, v2, v3 = d["v0"], d["v1"], d["v2"], d["v3"]
    stamp = "通过" if v["ok"] else "未过"
    waste_rows = _rows(v1["by_waste"], [
        ("", lambda r: r["waste"]),
        ("num", lambda r: f"{r['t']:.1f}"),
        ("num", lambda r: f"{r['q']:.0f}"),
        ("num", lambda r: f"{100 * r['share']:.1f}%"),
    ])
    assay_rows = _rows(v1["by_assay"], [
        ("", lambda r: r["assay"] + (" ·借" if r["proxy"] else "")),
        ("num", lambda r: f"{r['t']:.1f}"),
        ("num", lambda r: f"{r['q']:.0f}"),
    ])
    band_rows = _rows(v2["bands"], [
        ("", lambda r: r["band"]),
        ("num", lambda r: str(r["n"])),
        ("num", lambda r: f"{r['alpha']:.2f}"),
        ("num", lambda r: f"{r['qv_p2']:.0f}"),
        ("num", lambda r: f"{r['pred']:.0f}"),
        ("num", lambda r: f"{r['implied_isw']:.0f}"),
    ])

    def gate(x):
        if x["pass"] is None:
            cls, lab = "ref", "对照"
        elif x["pass"]:
            cls, lab = "ok", "PASS"
        else:
            cls, lab = "bad", "FAIL"
        return cls, lab

    g0, g2, g3 = gate(v0), gate(v2), gate(v3)
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>固废热值验证 · 日均值</title>
<style>
@import url("https://fonts.googleapis.com/css2?family=Archivo+Black&family=IBM+Plex+Mono:wght@400;500&display=swap");
:root {{ --paper:#e8e4dc; --ink:#141414; --mute:#5c5a54; --accent:#c45c26; --ok:#2f6b3a; }}
* {{ box-sizing:border-box; margin:0; padding:0; }}
body {{ font-family:"IBM Plex Mono",ui-monospace,monospace; font-size:13px; line-height:1.45;
  background:var(--paper); color:var(--ink); min-height:100dvh; }}
.wrap {{ max-width:1100px; margin:0 auto; padding:28px 32px 72px; }}
header {{ display:grid; grid-template-columns:1fr auto; gap:24px; border-bottom:3px solid var(--ink);
  padding-bottom:18px; margin-bottom:28px; }}
.kicker {{ letter-spacing:.18em; font-size:11px; text-transform:uppercase; color:var(--mute); }}
h1 {{ font-family:"Archivo Black",sans-serif; font-size:clamp(28px,5vw,48px); letter-spacing:-.04em;
  line-height:.92; text-transform:uppercase; margin-top:8px; }}
.stamp {{ text-align:right; align-self:end; }}
.stamp b {{ color:var(--accent); }}
.hero {{ display:grid; grid-template-columns:1.3fr 1fr; border:1px solid var(--ink); margin-bottom:28px; }}
.hero > div {{ padding:22px 24px; }}
.hero > div + div {{ border-left:1px solid var(--ink); }}
.big {{ font-family:"Archivo Black",sans-serif; font-size:clamp(44px,7vw,76px); letter-spacing:-.05em; line-height:.85; }}
.big small {{ display:block; font-size:.26em; letter-spacing:.12em; margin-top:10px; color:var(--mute); }}
.meta {{ margin-top:14px; color:var(--mute); max-width:56ch; }}
.grid3 {{ display:grid; grid-template-columns:1fr 1fr 1fr; border:1px solid var(--ink); margin-bottom:28px; }}
.grid3 > div {{ padding:16px 18px; border-right:1px solid var(--ink); }}
.grid3 > div:last-child {{ border-right:0; }}
.lbl {{ font-size:10px; letter-spacing:.16em; text-transform:uppercase; color:var(--mute); margin-bottom:8px; }}
.val {{ font-family:"Archivo Black",sans-serif; font-size:26px; }}
.sub {{ font-size:11px; color:var(--mute); margin-top:4px; }}
h2 {{ font-family:"Archivo Black",sans-serif; font-size:13px; letter-spacing:.14em; text-transform:uppercase;
  border-bottom:1px solid var(--ink); padding-bottom:8px; margin:28px 0 12px; }}
table {{ width:100%; border-collapse:collapse; }}
th,td {{ padding:6px 8px; border-bottom:1px solid #cfc9bc; }}
th {{ font-size:10px; letter-spacing:.12em; text-transform:uppercase; color:var(--mute); text-align:left; }}
.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
.ok {{ color:var(--ok); }} .bad {{ color:var(--accent); font-weight:500; }} .ref {{ color:var(--mute); }}
.split {{ display:grid; grid-template-columns:1fr 1fr; gap:28px; }}
.note {{ border-top:3px solid var(--ink); margin-top:36px; padding-top:16px; color:var(--mute);
  font-size:12px; max-width:80ch; }}
.note strong {{ color:var(--ink); font-weight:500; }}
code {{ font-family:inherit; background:#efebe3; padding:1px 4px; }}
@media (max-width:900px) {{
  header,.hero,.grid3,.split {{ grid-template-columns:1fr; }}
  .hero > div + div {{ border-left:0; border-top:1px solid var(--ink); }}
  .grid3 > div {{ border-right:0; border-bottom:1px solid var(--ink); }}
}}
</style>
</head>
<body>
<div class="wrap">
<header>
  <div>
    <div class="kicker">hhv-model / verify_isw_daily</div>
    <h1>综合固废<br/>验证链</h1>
  </div>
  <div class="stamp">
    {d['generated']}<br/>
    内部闸 <b>{stamp}</b><br/>
    <a href="showcase.html">反推展示</a>
  </div>
</header>

<section class="hero">
  <div>
    <div class="kicker">模型主推 · 解法 B</div>
    <div class="big">{d['q_isw']:.0f}<small>kJ/kg · {d['q_isw_kcal']:.0f} kcal · CI {d['ci_lo']:.0f}–{d['ci_hi']:.0f}</small></div>
    <p class="meta">{v['label']}。化验混合是样品口径，不进总判。</p>
  </div>
  <div>
    <div class="lbl">化验加权混合（对照）</div>
    <div class="val">{v1['q_mix']:.0f}</div>
    <div class="sub">{v1['msg']}</div>
    <div class="lbl" style="margin-top:16px">隐含入炉装修</div>
    <div class="val">{v1['implied_装修']:.0f}</div>
    <div class="sub">化验装修 1.9–2.4 万 · 口径不同</div>
  </div>
</section>

<div class="grid3">
  <div>
    <div class="lbl">{v0['name']}</div>
    <div class="val {g0[0]}">{g0[1]}</div>
    <div class="sub">{v0['msg']}</div>
  </div>
  <div>
    <div class="lbl">{v2['name']}</div>
    <div class="val {g2[0]}">{g2[1]}</div>
    <div class="sub">{v2['msg']}</div>
  </div>
  <div>
    <div class="lbl">{v3['name']}</div>
    <div class="val {g3[0]}">{g3[1]}</div>
    <div class="sub">{v3['msg']}</div>
  </div>
</div>

<h2>α 三分位 · 同口径</h2>
<table>
  <thead><tr><th>组</th><th class="num">周</th><th class="num">均α</th>
    <th class="num">二期Q混</th><th class="num">B预测</th><th class="num">隐含固废</th></tr></thead>
  <tbody>{band_rows}</tbody>
</table>

<div class="split">
  <div>
    <h2>VE 种类 × 化验</h2>
    <table>
      <thead><tr><th>种类</th><th class="num">吨</th><th class="num">Q</th><th class="num">占比</th></tr></thead>
      <tbody>{waste_rows}</tbody>
    </table>
  </div>
  <div>
    <h2>化验匹配</h2>
    <table>
      <thead><tr><th>来源</th><th class="num">吨</th><th class="num">Q</th></tr></thead>
      <tbody>{assay_rows}</tbody>
    </table>
    <p class="sub" style="margin-top:10px">覆盖 {100*v1['coverage']:.1f}% · 借样 {v1['proxy_tons']:.0f} t · {v1['days']}</p>
  </div>
</div>

<p class="note">
  <strong>复跑：</strong><code>python scripts/verify_isw_daily.py</code>
  硬闸：|B−C|&lt;{LIM_BC:.0f}；高α隐含固废落在模型 CI；
  踢 |残差|&gt;{LIM_RESID:.0%} 周后移动&lt;{LIM_KICK:.0f}。
  spearman(α, Q混−锚)={v2['spearman']:.2f}（一期锚被餐厨污染，不进闸）。
  化验是样品收到基，装修占固废 {100*v1['装修_t']/v1['tons_mapped']:.0f}%，
  不能用一张装修化验否定入炉综合数。
  丢掉周：{', '.join(v3['dropped']) or '无'}。
</p>
</div>
</body>
</html>
"""
    p = OUT / "verify_isw.html"
    p.write_text(html, encoding="utf-8")
    return p


def _out_dir(config_name: str) -> Path:
    stem = Path(config_name).name
    for suf in (".yaml", ".yml"):
        if stem.endswith(suf):
            stem = stem[: -len(suf)]
            break
    return ROOT / "outputs" / stem


def main() -> int:
    global OUT
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.snmis_daily.yaml")
    args = ap.parse_args()
    OUT = _out_dir(args.config)
    OUT.mkdir(parents=True, exist_ok=True)
    res = load_result()
    weekly = load_weekly()
    assays = load_assays()
    qb = res["ci_anchored"]["Q_工业固废"]
    qm = res["ci_anchored"]["Q_生活"]["mean"]
    q_isw = float(qb["mean"])
    assay = ve_weighted(assays)
    v0 = check_bc(res)
    v1 = check_assay(assay, q_isw, float(qb["lo"]), float(qb["hi"]))
    v2 = check_alpha(weekly, float(qm), q_isw, float(qb["lo"]), float(qb["hi"]))
    v3 = check_kick(weekly, q_isw)
    verd = verdict(v0, v2, v3, q_isw)
    payload = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "q_isw": q_isw,
        "q_isw_kcal": q_isw / KJ_PER_KCAL,
        "q_msw": float(qm),
        "ci_lo": float(qb["lo"]),
        "ci_hi": float(qb["hi"]),
        "v0": v0,
        "v1": v1,
        "v2": v2,
        "v3": v3,
        "verdict": verd,
    }
    # numpy / bool 可 JSON
    def conv(o):
        if isinstance(o, (np.floating,)):
            return float(o)
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.bool_,)):
            return bool(o)
        raise TypeError(type(o))
    (OUT / "verify_isw.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=conv), encoding="utf-8"
    )
    md = write_md(payload)
    ht = write_html(payload)
    print(f"总判 {'PASS' if verd['ok'] else 'FAIL'} — {verd['label']}")
    print(f"  {v0['msg']}")
    print(f"  {v2['msg']}")
    print(f"  {v3['msg']}")
    print(f"  {v1['msg']}")
    print(md)
    print(ht)
    return 0 if verd["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
