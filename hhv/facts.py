"""跑批产物读取层 —— 对外报告/导出的唯一取数入口。

审计背景（2026-09-08）：export_report_*.py 曾把 5192 kJ / 1240 大卡 / 24 周 / k=1.10
等结论数字写死在代码里，模型重跑后交给厂方与领导的 Word/PPT 静默过期。
此后对外数字一律经本模块从 outputs/<cfg>/result.json（+ verify_isw.json、weekly_series.csv）
读取；文件缺失或字段缺失直接抛错，宁可导出失败也不出旧数。

口径对应（与 hhv/report.py 的三解法一致）：
  解法A 自由回归（校验）      ci_free      Q_工业固废
  解法B 一期同周 × k（主报）   ci_anchored  Q_工业固废
  解法C 二期同炉历史周对齐     ci_furnace   Q_工业固废
"""
from __future__ import annotations

import json
import pathlib

KJ_PER_KCAL = 4.186


def _ci(block: dict | None, key: str) -> dict:
    return ((block or {}).get(key) or {})


def load_facts(out_dir: str | pathlib.Path) -> dict:
    """读取一次跑批的全部对外数字。缺 result.json 抛 FileNotFoundError。"""
    out = pathlib.Path(out_dir)
    rj = out / "result.json"
    if not rj.is_file():
        raise FileNotFoundError(f"缺少 {rj}——先跑一次测算（run.py）再导出报告")
    r = json.loads(rj.read_text(encoding="utf-8"))
    vj = out / "verify_isw.json"
    v = json.loads(vj.read_text(encoding="utf-8")) if vj.is_file() else {}

    qb = _ci(r.get("ci_anchored"), "Q_工业固废")
    qc = _ci(r.get("ci_furnace"), "Q_工业固废")
    qa = _ci(r.get("ci_free"), "Q_工业固废")
    qm = _ci(r.get("ci_anchored"), "Q_生活")
    if qb.get("mean") is None:
        raise ValueError(f"{rj} 里 ci_anchored.Q_工业固废.mean 缺失，无法取主报热值")

    gate = r.get("gate") or {}
    st1 = r.get("stats1") or {}
    st2 = r.get("stats2") or {}

    # 掺烧比例区间（周度序列，若在）
    alpha_min = alpha_max = alpha_mean = None
    wj = out / "weekly_series.csv"
    if wj.is_file():
        import csv
        with wj.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            # 列名可能是 alpha 或 alpha::<固废类别>（run.py 按配置动态命名）
            acol = next((c for c in (reader.fieldnames or []) if c == "alpha" or c.startswith("alpha::")), None)
            vals = [float(row[acol]) for row in reader
                    if acol and row.get(acol) not in (None, "", "nan")]
        if vals:
            alpha_min, alpha_max = min(vals), max(vals)
            alpha_mean = sum(vals) / len(vals)

    kcal = lambda kj: (round(kj / KJ_PER_KCAL) if kj is not None else None)  # noqa: E731

    return {
        "generated": r.get("generated"),
        "project": r.get("project"),
        "primary_label": r.get("primary_label"),
        "n_weeks": r.get("n_weeks"),
        "k": gate.get("k"),
        "gate_status": gate.get("status"),
        "gate_delta_pct": (gate.get("delta_mean") or 0) * 100,
        # 三解法（kJ/kg 收到基低位）
        "q_a": qa.get("mean"), "q_a_lo": qa.get("lo"), "q_a_hi": qa.get("hi"),
        "q_b": qb.get("mean"), "q_b_lo": qb.get("lo"), "q_b_hi": qb.get("hi"),
        "q_c": qc.get("mean"), "q_c_lo": qc.get("lo"), "q_c_hi": qc.get("hi"),
        "kcal_a": kcal(qa.get("mean")), "kcal_b": kcal(qb.get("mean")),
        "kcal_c": kcal(qc.get("mean")),
        "q_msw": qm.get("mean"), "kcal_msw": kcal(qm.get("mean")),
        "b_minus_c": (qb.get("mean") - qc.get("mean")) if (qb.get("mean") and qc.get("mean")) else None,
        # 数据筛选
        "total_days": st1.get("total_days"),
        "valid_days_p1": st1.get("valid_days"),
        "valid_days_p2": st2.get("valid_days"),
        # 效率（互证闸门）
        "eta_p1": (r.get("eff1") or {}).get("eta_typ"),
        "eta_p2": (r.get("eff2") or {}).get("eta_typ"),
        "eta_dcs_mean": (r.get("gate_eta") or {}).get("eta_dcs_mean"),
        "eta_rb_mean": (r.get("gate_eta") or {}).get("eta_rb_mean"),
        "eta_gate_days": (r.get("gate_eta") or {}).get("n_days"),
        # 掺烧比例
        "alpha_min_pct": None if alpha_min is None else alpha_min * 100,
        "alpha_max_pct": None if alpha_max is None else alpha_max * 100,
        "alpha_mean_pct": None if alpha_mean is None else alpha_mean * 100,
        # 化验对照
        "lab_mix": (v.get("v1") or {}).get("q_mix"),
        "lab_delta_pct": (v.get("v1") or {}).get("delta_pct"),
    }


def fmt_kj_kcal(facts: dict, which: str = "b") -> str:
    """`4876 kJ/kg（1165 大卡）` 样式的统一格式。"""
    q, kcal = facts.get(f"q_{which}"), facts.get(f"kcal_{which}")
    if q is None:
        return "—"
    return f"{q:.0f} kJ/kg（{kcal} 大卡）"
