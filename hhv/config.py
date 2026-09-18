"""配置加载与校验：YAML → 带默认值的嵌套 dict。"""
from __future__ import annotations

import copy
import pathlib

import yaml

DEFAULTS = {
    "project": "掺烧热值反推模型",
    "paths": {
        "phase1": None,        # 一期当期（纯烧）时序文件
        "phase2": None,        # 二期当期（掺烧）时序文件
        "phase2_hist": None,   # 二期两年前（纯烧，交叉验证，可缺省）
        "ledger": None,        # 台账（按日：入炉量/进厂量分来源）
        "dcs_hourly_db": None, # 二期 DCS 小时库（snmis_history.db）；空则仍用日报
        "output_dir": "outputs",
    },
    "dcs": {
        "enabled": False,
        "furnaces": ["f4", "f5", "f6"],
        "flow_shape_furnace": "f4",
        "on_furnace_t": 700.0,
        "on_steam_p": 2.0,
        "shape_min_flow": 5.0,
    },
    "sheet": None,             # Excel sheet 名（缺省取第一个）
    # 时序文件列名 → 标准列名 的映射（把右侧值改成你文件里的列名）
    "columns": {
        "time": "time",
        "steam_flow": "steam_flow",          # t/h 必需
        "steam_p": "steam_p",                # MPa 必需
        "steam_t": "steam_t",                # ℃ 必需
        "feedwater_t": "feedwater_t",        # ℃ 必需
        "o2": "o2",                          # % 可选（缺省→常数效率）
        "fluegas_t": "fluegas_t",            # ℃ 可选
        "spray_flow": None,                  # t/h 可选 减温水
        "blowdown_flow": None,               # t/h 可选 连续排污
    },
    "units": {
        "steam_p": "MPa_g",    # MPa_g 表压 | MPa_a 绝压
        "o2": "percent_dry",   # 干基体积百分数
    },
    # 台账列名映射
    "ledger_columns": {
        "date": "date",
        "phase1_input": "phase1_input",      # t/日 一期入炉量 必需
        "phase2_input": "phase2_input",      # t/日 二期入炉量 必需
        "msw_in": "msw_in",                  # t/日 生活垃圾进厂量 必需
        # 工业固废分来源进厂量（t/日）：来源名 → 台账列名
        "isw_sources": {},                   # 例 {"废布料": "isw_a", "污泥": "isw_b"}
    },
    "screening": {
        "min_load_frac": 0.70,       # 有效小时：负荷 ≥ 额定蒸发量 × 该系数（无额定则用中位数）
        "min_valid_hours": 20,       # 有效日：全天有效小时 ≥ 该值
        "step_jump": 0.30,           # 相邻小时流量变化 >30% 判启停/甩负荷
        "step_guard_hours": 2,       # 阶跃事件前后剔除小时数
        "min_valid_days_per_week": 5,
        "exclude_dates": [],         # 整日剔除（ISO 日期字符串），蒸汽与台账同步
    },
    "efficiency": {
        "mode": "reverse_balance",   # reverse_balance | constant
        "eta_constant": 0.82,
        # q2% = (k1*alpha_air + k2) * (T_排烟 - T_环境) / 100   （固体燃料经验式）
        "q2_k1": 3.55, "q2_k2": 0.44,
        "ambient_t": 20.0,
        "q4_default": 2.0,           # 无热灼减率数据时的 q4 %
        "q5_rated": 1.0,             # 额定负荷散热损失 %
        "q6_default": 0.2,
    },
    "boilers": {
        "phase1": {"name": "一期", "rated_flow": 40.0},   # t/h
        "phase2": {"name": "二期", "rated_flow": 32.0},
    },
    "masses": {
        "lag_days_msw": 3,           # 生活垃圾进厂→入炉 滞后天数（周尺度对齐用）
        "lag_days_isw": 0,
    },
    "regression": {
        "bootstrap": 2000,
        "ci": 0.95,
        "huber_t": 1.345,
    },
    "calibration": {
        "gate1": 0.05,               # <5% 主锚通过
        "gate2": 0.10,               # 5~10% 双锚并报；>10% 预警
    },
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: str | pathlib.Path) -> dict:
    p = pathlib.Path(path)
    if not p.exists():
        raise FileNotFoundError(f"配置文件不存在: {p}")
    with open(p, encoding="utf-8") as f:
        user = yaml.safe_load(f) or {}
    cfg = _merge(DEFAULTS, user)
    _validate(cfg)
    return cfg


def _validate(cfg: dict) -> None:
    paths = cfg["paths"]
    for key in ("phase1", "phase2", "ledger"):
        if not paths.get(key):
            raise ValueError(f"paths.{key} 未配置（必需）")
    for key in ("phase1", "phase2"):
        if not pathlib.Path(paths[key]).exists():
            raise FileNotFoundError(f"paths.{key} 指向的文件不存在: {paths[key]}")
    if not pathlib.Path(paths["ledger"]).exists():
        raise FileNotFoundError(f"paths.ledger 指向的文件不存在: {paths['ledger']}")
    if paths.get("phase2_hist") and not pathlib.Path(paths["phase2_hist"]).exists():
        raise FileNotFoundError(f"paths.phase2_hist 指向的文件不存在: {paths['phase2_hist']}")
    if cfg.get("dcs", {}).get("enabled"):
        db = paths.get("dcs_hourly_db")
        if not db:
            raise ValueError("dcs.enabled 时必须配置 paths.dcs_hourly_db")
        if not pathlib.Path(db).exists():
            raise FileNotFoundError(f"paths.dcs_hourly_db 不存在: {db}")
    if cfg["efficiency"]["mode"] not in ("reverse_balance", "constant"):
        raise ValueError("efficiency.mode 必须是 reverse_balance 或 constant")
    if not cfg["ledger_columns"]["isw_sources"]:
        # 未分来源时按单一综合来源处理
        cfg["ledger_columns"]["isw_sources"] = {"工业固废综合": None}
