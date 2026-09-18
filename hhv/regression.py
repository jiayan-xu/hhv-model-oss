"""回归分解层：Huber 稳健回归 + Bootstrap 置信区间。

模型（周尺度）：
  Q_混(w) = Q_生活(w) + Σ_j β_j × α_j(w)
  Q_j = mean(Q_生活) + β_j
锚定解的截距可以是标量或与周对齐的序列（同周相减）。
两种解法：
  A. 自由解：直接 Huber 回归
  B. 锚定解：β0(w) 为同期一期×k 或二期历史周对齐，只解斜率
Bootstrap 对周重采样，得到各参数 CI。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import statsmodels.api as sm
from statsmodels.robust.norms import HuberT


def _huber_fit(y: np.ndarray, X: np.ndarray, t: float):
    res = sm.RLM(y, X, M=HuberT(t=t)).fit()
    return np.asarray(res.params)


def _as_anchor_array(q_msw_anchor, n: int) -> np.ndarray:
    a = np.asarray(q_msw_anchor, dtype=float)
    if a.ndim == 0 or a.size == 1:
        return np.full(n, float(np.reshape(a, -1)[0]))
    if a.size != n:
        raise ValueError(f"锚序列长度 {a.size} 与样本 {n} 不一致")
    return np.reshape(a, -1)


def fit_free(qmix: pd.Series, alphas: pd.DataFrame, rcfg: dict) -> dict:
    """解法A：自由 Huber 回归 Q_混 = β0 + Σ β_j α_j。"""
    X = np.column_stack([np.ones(len(qmix))] + [alphas[c].values for c in alphas])
    beta = _huber_fit(qmix.values, X, rcfg.get("huber_t", 1.345))
    yhat = X @ beta
    resid = qmix.values - yhat
    return {"beta": beta, "resid": resid, "yhat": yhat}


def fit_anchored(qmix: pd.Series, alphas: pd.DataFrame, q_msw_anchor,
                 rcfg: dict) -> dict:
    """解法B/C：时变或常数截距，只解斜率。"""
    n = len(qmix)
    anchor = _as_anchor_array(q_msw_anchor, n)
    y = qmix.values - anchor
    X = np.column_stack([alphas[c].values for c in alphas])
    if X.shape[1] == 0:
        raise ValueError("无掺烧来源列")
    slope = _huber_fit(y, X, rcfg.get("huber_t", 1.345))
    yhat = anchor + X @ slope
    resid = qmix.values - yhat
    beta = np.concatenate([[float(np.mean(anchor))], slope])
    return {"beta": beta, "resid": resid, "yhat": yhat, "anchor": anchor}


def bootstrap_ci(qmix: pd.Series, alphas: pd.DataFrame, rcfg: dict,
                 anchored: bool = False, q_msw_anchor=None,
                 anchor_series: pd.Series = None) -> dict:
    """周重采样 Bootstrap：返回每个参数的均值/CI 与 Q_j 的 CI。

    anchored 且提供 anchor_series 时，每次重采样同步抽取对应周锚（同周相减）。
    """
    n = len(qmix)
    B = int(rcfg.get("bootstrap", 2000))
    ci = rcfg.get("ci", 0.95)
    idx = np.arange(n)
    rng = np.random.default_rng(42)
    betas = []
    for _ in range(B):
        pick = rng.choice(idx, size=n, replace=True)
        try:
            if anchored and (q_msw_anchor is not None or anchor_series is not None):
                if anchor_series is not None:
                    a = anchor_series.iloc[pick]
                else:
                    a = q_msw_anchor
                r = fit_anchored(qmix.iloc[pick], alphas.iloc[pick], a, rcfg)
            else:
                r = fit_free(qmix.iloc[pick], alphas.iloc[pick], rcfg)
            betas.append(r["beta"])
        except Exception:
            continue
    betas = np.asarray(betas)
    if betas.size == 0:
        raise RuntimeError("Bootstrap 全部失败，检查样本周数/α 列")
    lo, hi = np.percentile(betas, [(1 - ci) / 2 * 100, (1 + ci) / 2 * 100], axis=0)
    names = ["Q_生活"] + [f"Q_{c.replace('alpha::', '')}" for c in alphas]
    out = {}
    for i, nm in enumerate(names):
        out[nm] = {"mean": float(betas[:, i].mean()),
                   "lo": float(lo[i]), "hi": float(hi[i])}
    for j in range(1, betas.shape[1]):
        qj = betas[:, 0] + betas[:, j]
        nm = names[j]
        out[nm] = {"mean": float(qj.mean()),
                   "lo": float(np.percentile(qj, (1 - ci) / 2 * 100)),
                   "hi": float(np.percentile(qj, (1 + ci) / 2 * 100))}
    out["_n_boot"] = len(betas)
    return out
