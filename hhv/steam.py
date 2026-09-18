"""蒸汽/水焓值计算：优先 IAPWS-IF97（iapws 库），失败时退回内置蒸汽表插值。

所有焓值单位 kJ/kg。主蒸汽按过热蒸汽 (P, T)；给水按未饱和水近似
（h ≈ 4.186×T，压力修正可忽略）；炉渣/排污按汽包压力下饱和水。
"""
from __future__ import annotations

try:
    from iapws import IAPWS97

    _HAS_IAPWS = True
except Exception:  # pragma: no cover
    _HAS_IAPWS = False

# 内置过热蒸汽表（kJ/kg），线性插值兜底。来源：常用蒸汽表（中压区）。
# 行=绝对压力 MPa，列=温度 ℃。
_TABLE_T = [300, 350, 400, 450, 500, 550]
_TABLE = {
    1.0: [3051.6, 3157.7, 3263.9, 3371.1, 3478.9, 3588.1],
    2.0: [3024.2, 3136.5, 3247.9, 3358.4, 3468.0, 3578.0],
    3.0: [2994.3, 3115.8, 3230.9, 3343.6, 3456.5, 3569.5],
    4.0: [2960.9, 3093.9, 3213.6, 3329.2, 3445.3, 3559.5],
    5.0: [2925.3, 3069.3, 3195.7, 3316.2, 3434.2, 3552.0],
    6.0: [2884.2, 3040.4, 3177.2, 3301.0, 3422.2, 3542.7],
    8.0: [2785.0, 2988.9, 3138.0, 3276.0, 3410.0, 3542.0],
    10.0: [2725.0, 2925.0, 3098.0, 3241.0, 3376.0, 3508.0],
}


def h_steam(p_mpa: float, t_c: float) -> float:
    """过热蒸汽焓 h(P, T)，P 为绝对压力 MPa。"""
    if _HAS_IAPWS:
        try:
            return float(IAPWS97(P=max(p_mpa, 0.002), T=t_c + 273.15).h)
        except Exception:
            pass
    return _table_interp(p_mpa, t_c)


def h_feedwater(t_c: float) -> float:
    """未饱和给水焓近似：h ≈ cp × T，cp=4.186。"""
    return 4.186 * t_c


def h_satliquid(p_mpa: float) -> float:
    """饱和水焓（排污/炉渣物理热估算用）。"""
    if _HAS_IAPWS:
        try:
            return float(IAPWS97(P=max(p_mpa, 0.002), x=0).h)
        except Exception:
            pass
    # 近似：中低压区 h' ≈ 761 + 497×ln(P)（P in MPa, 0.5~10 MPa 误差 <3%）
    import math
    return 761.0 + 497.0 * math.log(max(p_mpa, 0.5))


def _table_interp(p_mpa: float, t_c: float) -> float:
    import math
    if not (math.isfinite(p_mpa) and math.isfinite(t_c)):
        return float("nan")
    ps = sorted(_TABLE)
    t0 = max(_TABLE_T[0], min(_TABLE_T[-1], t_c))
    ti = max(i for i in range(len(_TABLE_T) - 1) if _TABLE_T[i] <= t0)
    frac_t = (t0 - _TABLE_T[ti]) / (_TABLE_T[ti + 1] - _TABLE_T[ti])
    if p_mpa <= ps[0]:
        lo = hi = ps[0]
    elif p_mpa >= ps[-1]:
        lo = hi = ps[-1]
    else:
        lo = max(p for p in ps if p <= p_mpa)
        hi = min(p for p in ps if p >= p_mpa)
    def row_at(p):
        r = _TABLE[lo if p == lo else hi]
        return r[ti] + frac_t * (r[ti + 1] - r[ti])
    if lo == hi:
        return row_at(lo)
    frac_p = (p_mpa - lo) / (hi - lo)
    return row_at(lo) + frac_p * (row_at(hi) - row_at(lo))


def enthalpy_source() -> str:
    return "IAPWS-IF97 (iapws)" if _HAS_IAPWS else "内置蒸汽表插值（建议安装 iapws）"
