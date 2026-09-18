"""热值软测量 v1：二期入炉垃圾热值（plant_q）日尺度预测。

标签：SNMIS 日报厂填热值（plant_q_p2，2026-02~09 共 ~213 天，口径待验）；
特征：DCS 小时库日聚合（二期 3 炉 6 测点）+ 掺烧比例近似 + 月份季节项；
验证：TimeSeriesSplit(5) + 基线对比（月度均值/上周同期）。

用法：python scripts/train_softsensor.py
输出：outputs/softsensor/{metrics.json, feature_importance.csv, model.pkl}
"""
from __future__ import annotations

import json
import pickle
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import TimeSeriesSplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # 直接运行时 hhv 包可导入

from hhv import db as _db  # noqa: E402
DB = ROOT / "data" / "snmis_history.db"
OUT = ROOT / "outputs" / "softsensor"


def load_daily_ops(conn: sqlite3.Connection) -> pd.DataFrame:
    """二期 3 炉小时数据 → 日度特征表。"""
    df = pd.read_sql_query(
        "SELECT day, furnace, point, mean FROM hourly_hourly WHERE mean IS NOT NULL",
        conn)
    pv = df.pivot_table(index="day", columns=["furnace", "point"],
                        values="mean", aggfunc="mean")
    pv.columns = [f"{f}_{p}" for f, p in pv.columns]
    pv.index = pd.to_datetime(pv.index)
    # 派生：二期主汽流量均
    flow_cols = [c for c in pv.columns if "主汽流量" in c]
    if flow_cols:
        pv["二期主汽流量均"] = pv[flow_cols].mean(axis=1)
    # 派生：4#掺烧占比近似
    total = pv[flow_cols].sum(axis=1)
    pv["f4_掺烧占比"] = (pv["f4_主汽流量"] / total).where(total > 0)
    # 日历特征
    pv["月份"] = pv.index.month
    return pv


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    conn = _db.connect(DB)   # WAL + busy_timeout + 索引
    feat = load_daily_ops(conn)
    conn.close()

    # 标签：厂填二期垃圾热值（ledger_daily / ledger_hist_daily）
    lab_frames = []
    for f in ("data/plant/ledger_daily.csv", "data/plant/ledger_hist_daily.csv"):
        p = ROOT / f
        if p.exists():
            d = pd.read_csv(p)
            d["time"] = pd.to_datetime(d["date"])
            col = "plant_q_p2" if "plant_q_p2" in d.columns else "plant_q_p1"
            s = d.set_index("time")[col].dropna()
            lab_frames.append(s)
    y = pd.concat(lab_frames).sort_index() if lab_frames else pd.Series(dtype=float)

    data = feat.join(y.rename("y"), how="inner").dropna(subset=["y"]).sort_index()
    print(f"样本: {len(data)} 天 ({data.index.min():%Y-%m-%d} ~ {data.index.max():%Y-%m-%d})")

    exclude = {"y"}
    feature_cols = [c for c in data.columns if c not in exclude]
    X = data[feature_cols].values
    yy = data["y"].values

    tscv = TimeSeriesSplit(n_splits=5)
    months = data.index.month
    maes, r2s, base_maes = [], [], []
    for tr, te in tscv.split(X):
        m = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.08, random_state=7)
        m.fit(X[tr], yy[tr])
        p = m.predict(X[te])
        maes.append(mean_absolute_error(yy[te], p))
        r2s.append(r2_score(yy[te], p))
        # 基线必须同样 out-of-sample：用训练折的月度均值预测测试折（月份缺失回退训练折整体均值）。
        # 旧写法在**全样本内**算月均值，基线被未来信息抬高，才印出“提升 2%”的假象。
        tr_month_mean = pd.Series(yy[tr], index=months[tr]).groupby(level=0).mean()
        fallback = float(np.mean(yy[tr]))
        base_pred = np.array([tr_month_mean.get(mm, fallback) for mm in months[te]])
        base_maes.append(mean_absolute_error(yy[te], base_pred))
    print("\n=== 时间序列切分验证 ===")
    for i, (a, r2, b) in enumerate(zip(maes, r2s, base_maes), 1):
        print(f"  fold{i}: MAE {a:.0f} kJ/kg | R2 {r2:.3f} | 基线(月均,折内) MAE {b:.0f}")
    print(f"  平均: MAE {np.mean(maes):.0f} kJ/kg | R2 {np.mean(r2s):.3f} | 基线 MAE {np.mean(base_maes):.0f}")

    m = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.08, random_state=7)
    m.fit(X, yy)
    from sklearn.inspection import permutation_importance
    pi = permutation_importance(m, X, yy, n_repeats=10, random_state=7)
    imp = sorted(zip(feature_cols, pi.importances_mean), key=lambda x: -x[1])[:10]
    print("\n=== 特征重要性 Top10（训练集 permutation，仅供参考）===")
    for name, v in imp:
        print(f"  {name:<28} {v:.0f}")

    base_mae = float(np.mean(base_maes))
    cv_r2 = float(np.mean(r2s))
    lift = 100 * (1 - np.mean(maes) / base_mae) if base_mae else float("nan")
    usable = cv_r2 > 0 and lift > 0
    print(f"\n基线A（月度均值，折内 out-of-sample）: MAE {base_mae:.0f} kJ/kg；"
          f"模型 MAE {np.mean(maes):.0f}（比基线A好 {lift:.0f}%）")
    print(f"基线B（测试折自身均值，即 R² 的参照）: R²={cv_r2:.3f}"
          f"{'（模型优于基线B）' if cv_r2 > 0 else '（模型差于基线B：跟不上水平漂移）'}")
    if usable:
        print("结论：优于两条基线，软测量可作参考。")
    else:
        print("结论：**当前不可用**——两条基线未同时优于（见上），不得接入定价或展示；"
              "标签为厂填热值（循环推算值），需等化验锚定数据积累后重训。")

    metrics = {
        "n_days": len(data),
        "window": [str(data.index.min().date()), str(data.index.max().date())],
        "cv_mae_kJkg": round(float(np.mean(maes)), 1),
        "cv_r2": round(cv_r2, 3),
        "baseline_monthly_mean_mae": round(base_mae, 1),
        "baseline": "月度均值（折内 out-of-sample）",
        "lift_pct": round(float(lift), 1),
        "usable": usable,
        "feature_importance_top10": [[n, round(float(v), 1)] for n, v in imp],
        "label_provenance": "SNMIS日报厂填热值(plant_q_p2)，口径待验（循环推算值，非独立标签）",
    }
    (OUT / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=1), encoding="utf-8")
    pd.DataFrame(imp, columns=["feature", "importance"]).to_csv(
        OUT / "feature_importance.csv", index=False, encoding="utf-8-sig")
    print(f"written: {OUT / 'metrics.json'}")
    return 0 if (usable or "--allow-bad" in sys.argv) else 1


if __name__ == "__main__":
    sys.exit(main())
