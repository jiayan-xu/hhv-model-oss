"""SQLite 连接与索引的统一入口。

背景（2026-09-08 审计）：hourly_hourly（35.6 万行）只有 (code, day, hour) 主键，
按 point / furnace 过滤的两条主查询全表扫描；库默认 journal_mode=delete，
拉数脚本连续写库 13 小时期间跑批读库可能遇到 database is locked。

本模块把「WAL + busy_timeout + 必要索引」收敛成一处，所有读写连接都经 connect()。
"""
from __future__ import annotations

import pathlib
import sqlite3

# 主查询用到的过滤维度（见 hhv/calib.py 的 point='LHV' 分组、hhv/dcs.py 的 furnace+day 区间）
INDEXES = (
    ("idx_hh_point_day", "hourly_hourly(point, day)"),
    ("idx_hh_furnace_day", "hourly_hourly(furnace, day)"),
)


def connect(path: str | pathlib.Path, *, read_only: bool = False) -> sqlite3.Connection:
    """打开库并统一设置 WAL / busy_timeout。

    WAL 是库级持久设置（写一次即生效），busy_timeout 是连接级——两者都设，
    保证拉数写库与跑批读库并发时不会立刻抛 locked。
    """
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
    except sqlite3.DatabaseError:
        # 只读挂载/损坏库等场景不阻断主流程
        pass
    if not read_only:
        ensure_indexes(conn)
    return conn


def ensure_indexes(conn: sqlite3.Connection) -> None:
    """补齐主查询索引（幂等）。表不存在时静默跳过（首次建库由调用方建表）。"""
    has_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='hourly_hourly'").fetchone()
    if not has_table:
        return
    for name, cols in INDEXES:
        conn.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {cols}")
    conn.commit()
