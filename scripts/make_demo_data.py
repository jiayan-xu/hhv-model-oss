"""合成演示数据：正向物理仿真，带已知真值，用于验证整条反推流水线。

生成 data/demo/ 下：
  phase1_2026.csv  一期当期（纯烧生活垃圾）
  phase2_2026.csv  二期当期（掺烧，蒸汽流量计带 +0.6% 偏差）
  phase2_2024.csv  二期两年前（纯烧，同表计偏差）
  ledger_2026.csv / ledger_2024.csv  台账（进厂量按滞后平移）
  truth.json       真值（用于比对反推结果）

真值设定：Q_生活≈6350（窗口均值，随季节缓升）；废布料 12000；污泥 4500；
真实效率 一期0.815 / 二期0.822（与模型反平衡默认参数故意留差，演示系统误差量级）。
"""
from __future__ import annotations

import json
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hhv import steam  # noqa: E402

RNG = np.random.default_rng(7)
ROOT = pathlib.Path(__file__).resolve().parents[1]
DEMO = ROOT / "data" / "demo"
DEMO.mkdir(parents=True, exist_ok=True)

H_MS = steam.h_steam(4.0, 400.0)   # 3214.4 kJ/kg
H_FW = steam.h_feedwater(104.0)    # ≈435 kJ/kg
DH = H_MS - H_FW

ETA1, ETA2 = 0.815, 0.822          # 真实效率
METER2_BIAS = 1.006               # 二期蒸汽流量计偏差
Q_ISW = {"废布料": 12000.0, "污泥": 4500.0}


def q_msw_2026(dates: pd.DatetimeIndex) -> np.ndarray:
    """生活垃圾热值：6150→6500 缓升 + 小幅季节项 + 日噪声。"""
    t = (dates - dates[0]).days / max((dates[-1] - dates[0]).days, 1)
    doy = dates.dayofyear.values
    base = 6150 + 350 * t + 120 * np.sin(2 * np.pi * (doy - 30) / 365)
    return base + RNG.normal(0, 110, len(dates))


def load_profile(days: int) -> np.ndarray:
    """日负荷系数：常态 0.88~0.97，掺入启停/低负荷事件。"""
    lf = np.clip(RNG.normal(0.93, 0.035, days), 0.84, 0.99)
    idx = RNG.choice(days, size=max(3, days // 18), replace=False)
    lf[idx] = RNG.uniform(0.30, 0.55, len(idx))          # 启停/甩负荷日
    idx2 = RNG.choice(days, size=2, replace=False)
    lf[idx2] = 0.04                                        # 停炉日
    return lf


def gen_boiler_csv(path, dates, mass_t, q_ar, eta, rated_flow, meter_bias=1.0):
    """按时序正向生成一台炉的小时数据。

    物理自洽约定：日负荷系数 lf 同时缩放入炉质量与蒸汽流量，
    即当日实际入炉量 = mass_t × lf（台账按此写），返回 lf 数组。
    """
    lfs = load_profile(len(dates))
    rows = []
    for d, m, q, lf in zip(dates, mass_t, q_ar, lfs):
        m_burn = m * lf                                   # 当日实际入炉 t
        d_mean = m_burn * 1000.0 * eta * q / 24.0 / DH / 1000.0  # t/h
        for h in range(24):
            if lf < 0.1:                        # 停炉日：小流量
                flow = RNG.uniform(1, 4)
            elif lf < 0.6:                      # 启停日：爬坡形状
                shape = min(1.0, 0.25 + 0.75 * h / 18.0)
                flow = d_mean * shape * RNG.normal(1, 0.05)
            else:
                flow = d_mean * RNG.normal(1, 0.03)
            rows.append({
                "time": d + pd.Timedelta(hours=h),
                "steam_flow": round(max(flow * meter_bias, 0.5), 3),
                "steam_p": round(RNG.normal(3.90, 0.05), 4),      # 表压 MPa
                "steam_t": round(RNG.normal(400, 3), 2),
                "feedwater_t": round(RNG.normal(104, 1.5), 2),
                "o2": round(np.clip(RNG.normal(8.0, 0.8), 4, 15), 2),
                "fluegas_t": round(RNG.normal(190, 10), 1),
            })
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")
    return lfs


def main():
    dates26 = pd.date_range("2026-02-01", "2026-06-30", freq="D")
    dates24 = pd.date_range("2024-02-01", "2024-06-30", freq="D")
    n = len(dates26)

    # ── 一期 2026（纯烧）──
    q1 = q_msw_2026(dates26)
    m1 = np.clip(RNG.normal(510, 20, n), 450, 570)
    lf1 = gen_boiler_csv(DEMO / "phase1_2026.csv", dates26, m1, q1, ETA1, 40)
    m1_burn = m1 * lf1                                   # 实际入炉（台账）

    # ── 二期 2026（掺烧）──
    weeks = pd.Series(dates26.isocalendar().week.astype(int).values, index=dates26)
    alpha_a = {w: RNG.uniform(0.05, 0.18) for w in weeks.unique()}
    alpha_b = {w: RNG.uniform(0.02, 0.12) for w in weeks.unique()}
    aA = weeks.map(alpha_a).values
    aB = weeks.map(alpha_b).values
    m2 = np.clip(RNG.normal(380, 15, n), 330, 420)
    qmix = (1 - aA - aB) * q1 + aA * Q_ISW["废布料"] + aB * Q_ISW["污泥"]
    lf2 = gen_boiler_csv(DEMO / "phase2_2026.csv", dates26, m2, qmix, ETA2, 32,
                         meter_bias=METER2_BIAS)
    m2_burn = m2 * lf2

    # ── 二期 2024（纯烧，交叉验证）──
    q1h = q_msw_2026(dates24) - 120.0          # 两年漂移：略低
    m2h = np.clip(RNG.normal(375, 15, len(dates24)), 330, 420)
    lf2h = gen_boiler_csv(DEMO / "phase2_2024.csv", dates24, m2h, q1h, ETA2, 32,
                          meter_bias=METER2_BIAS)
    m2h_burn = m2h * lf2h
    m1h = np.clip(RNG.normal(500, 18, len(dates24)), 440, 560)
    lf1h = RNG.normal(0.93, 0.03, len(dates24))          # 2024 一期未生成时序，仅台账
    m1h_burn = m1h * np.clip(lf1h, 0.85, 0.99)

    # ── 台账 2026 ──（进厂 → 入炉：生活垃圾滞后 3 天，工业固废 0 天）
    led = pd.DataFrame({
        "date": dates26.strftime("%Y-%m-%d"),
        "phase1_input": np.round(m1_burn, 1),
        "phase2_input": np.round(m2_burn, 1),
        "msw_in": 0.0,
        "isw_a": np.round(aA * m2_burn, 1),
        "isw_b": np.round(aB * m2_burn, 1),
    })
    msw_burned = m1_burn + (1 - aA - aB) * m2_burn          # 入炉生活垃圾
    msw_in = np.concatenate([msw_burned[3:], msw_burned[-3:]])  # 进厂早 3 天
    led["msw_in"] = np.round(msw_in, 1)
    led.to_csv(DEMO / "ledger_2026.csv", index=False, encoding="utf-8-sig")

    # ── 台账 2024 ──
    led24 = pd.DataFrame({
        "date": dates24.strftime("%Y-%m-%d"),
        "phase1_input": np.round(m1h_burn, 1),
        "phase2_input": np.round(m2h_burn, 1),
        "msw_in": 0.0, "isw_a": 0.0, "isw_b": 0.0,
    })
    msw_in24 = np.concatenate([m2h_burn[3:], m2h_burn[-3:]])
    led24["msw_in"] = np.round(led24["phase1_input"].values + msw_in24, 1)
    led24.to_csv(DEMO / "ledger_2024.csv", index=False, encoding="utf-8-sig")

    # ── 真值 ──
    truth = {
        "Q_生活": float(np.average(q1, weights=m1_burn)),
        "Q_废布料": Q_ISW["废布料"],
        "Q_污泥": Q_ISW["污泥"],
    }
    (DEMO / "truth.json").write_text(
        json.dumps(truth, ensure_ascii=False, indent=2), encoding="utf-8")
    print("demo data written to", DEMO)
    print("truth:", truth)


if __name__ == "__main__":
    main()
