"""SNMIS 生产日报全量回填 → SQLite（下载→解析→入库 一条管道）。

用法：
  python scripts/backfill_snmis_db.py                 # 全量：先解析本地已有文件，再回填 2005-01~2023-12
  python scripts/backfill_snmis_db.py --start 2015-01 # 指定回填起点

产物：data/snmis_history.db
  表 daily(日期×两期 日均值) / months(月份台账，断点续传) / meta
质检：按年输出覆盖率、吨发比、热值合理性，标记模板可能不一致的可疑年。

安全边界：只读 GET（FineReport 免登录导出，与日常监控同一端点）；
1.5s 请求间隔；不写 SNMIS 任何数据；断点续传（重跑自动跳过已完成月份）。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_snmis_daily import parse_phase_sheet  # 复用现成解析器
from fetch_snmis_daily import fetch_month

RAW = ROOT / "data" / "snmis_raw"
DB = ROOT / "data" / "snmis_history.db"
PHASES = ("p1", "p2")
BACKFILL_START_DEFAULT = (2005, 1)
SLEEP_SECS = 1.5

DDL = """
CREATE TABLE IF NOT EXISTS daily (
  date TEXT NOT NULL, phase TEXT NOT NULL,
  steam_flow REAL, steam_total_t REAL, phase_input REAL, plant_in REAL,
  kitchen REAL, plant_q REAL, n_furnaces_on INTEGER, source TEXT,
  PRIMARY KEY (date, phase));
CREATE TABLE IF NOT EXISTS months (
  ym TEXT PRIMARY KEY, status TEXT NOT NULL, rows INTEGER, kb INTEGER,
  source TEXT, fetched_at TEXT);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def connect() -> sqlite3.Connection:
    from hhv import db as _db  # noqa: E402
    conn = _db.connect(DB)
    conn.executescript(DDL)
    return conn


def existing_months(conn: sqlite3.Connection) -> set[str]:
    """已成功入库的月份。error/empty 不算（网络失败可重试），
    template_echo 算终态（再拉还是同一份模板填充，避免无谓重拉）。"""
    return {r[0] for r in conn.execute(
        "SELECT ym FROM months WHERE status IN ('ok','template_echo')")}


def parse_and_store(conn: sqlite3.Connection, wb_path: Path, year: int, month: int,
                    source: str) -> int:
    """解析一个月度工作簿（本地文件或下载内容）→ 入库。返回行数。

    模板回声防护：FineReport 对无真实数据的年份返回同一份模板填充值
    （2005~2023 实测全部为同一模板，逐年数值完全相同）。入库前比对
    当月热值序列指纹与 meta.template_hashes 中该月指纹，命中则不入库。
    """
    from openpyxl import load_workbook
    try:
        wb = load_workbook(wb_path, data_only=True)
    except Exception as e:
        print(f"  !! {wb_path.name} 打不开: {e}")
        return 0
    sig_rows = conn.execute(
        "SELECT key, value FROM meta WHERE key LIKE 'template_hash_%'").fetchall()
    sigs = {r[0]: r[1] for r in sig_rows}
    key = f"template_hash_{month:02d}"
    n = 0
    for sn, phase in (("一期", "p1"), ("二期", "p2")):
        if sn not in wb.sheetnames:
            continue
        df = parse_phase_sheet(wb[sn], year, month, phase)
        if key in sigs and phase == "p1":
            import hashlib
            h = hashlib.md5(",".join(f"{v:.3f}" for v in df["plant_q"].fillna(0)).encode()).hexdigest()
            if h == sigs[key]:
                print(f"  !! {year}-{month:02d} 命中模板回声指纹，拒绝入库")
                return -1
        for _, r in df.iterrows():
            steam_total = r["steam_flow"] * 24.0
            n_on = 3 if steam_total > 0 else 0
            conn.execute(
                "INSERT OR REPLACE INTO daily VALUES (?,?,?,?,?,?,?,?,?,?)",
                (r["time"], phase, r["steam_flow"], steam_total, r["phase_input"],
                 r["plant_in"], r["kitchen"], r["plant_q"], n_on, source))
            n += 1
    conn.commit()
    return n


def mark(conn: sqlite3.Connection, ym: str, status: str, rows: int,
         kb: int, source: str):
    conn.execute("INSERT OR REPLACE INTO months VALUES (?,?,?,?,?,datetime('now','localtime'))",
                 (ym, status, rows, kb, source))
    conn.commit()


def backfill_http(conn: sqlite3.Connection, start: tuple[int, int],
                  end: tuple[int, int]) -> None:
    y, m = start
    while (y, m) <= end:
        ym = f"{y}-{m:02d}"
        if ym in existing_months(conn):
            print(f"{ym}: 已入库，跳过")
        else:
            try:
                p = fetch_month(y, m)
                kb = p.stat().st_size // 1024 if p.exists() else 0
                if p.exists() and kb * 1024 >= 8000:
                    n = parse_and_store(conn, p, y, m, "http")
                    if n < 0:
                        mark(conn, ym, "template_echo", 0, kb, "http")
                        print(f"  {ym}: 模板回声，拒绝入库")
                    else:
                        mark(conn, ym, "ok", n, kb, "http")
                        print(f"  {ym}: {n} 行, {kb} KB")
                else:
                    mark(conn, ym, "empty", 0, kb, "http")
                    print(f"  {ym}: 空/无数据")
            except Exception as e:
                mark(conn, ym, "error", 0, 0, "http")
                print(f"  {ym}: ERROR {str(e)[:80]}")
            time.sleep(SLEEP_SECS)
        m += 1
        if m > 12:
            y, m = y + 1, 1


def backfill_local(conn: sqlite3.Connection) -> None:
    """解析本地已有的 dailyReport01_*.xlsx（2024+ 那批）。"""
    import re
    done = existing_months(conn)
    for p in sorted(RAW.glob("dailyReport01_*.xlsx")):
        mm = re.match(r"dailyReport01_(\d{4})-(\d{2})\.xlsx", p.name)
        if not mm:
            continue
        ym = f"{mm.group(1)}-{mm.group(2)}"
        if ym in done:
            continue
        n = parse_and_store(conn, p, int(mm.group(1)), int(mm.group(2)), "local")
        mark(conn, ym, "ok", n, p.stat().st_size // 1024, "local")
        print(f"{ym}: 本地文件 {n} 行")


def qc(conn: sqlite3.Connection) -> None:
    print("\n" + "=" * 76)
    print("质检：按年覆盖率与合理性（可疑年 = 模板可能不一致，需人工核对行号）")
    print("=" * 76)
    rows = pd.read_sql_query(
        """SELECT substr(date,1,4) y, phase,
                  COUNT(*) days,
                  ROUND(AVG(steam_flow),1) avg_flow,
                  ROUND(MEDIAN(NULLIF(plant_q,0)),0) med_q,
                  ROUND(AVG(CASE WHEN phase_input>0 THEN steam_total_t/phase_input END),2) t_per_t
           FROM daily GROUP BY y, phase ORDER BY y, phase""", conn)
    print(rows.to_string(index=False))
    bad = rows[(rows["med_q"].notna()) & ((rows["med_q"] < 3000) | (rows["med_q"] > 12000))]
    bad2 = rows[(rows["t_per_t"].notna()) & ((rows["t_per_t"] < 1.5) | (rows["t_per_t"] > 5.5))]
    suspects = sorted(set(bad["y"]) | set(bad2["y"]))
    if suspects:
        print(f"\n⚠️ 可疑年份（热值/吨发出界，模板或炉数可能不同）: {suspects}")
    else:
        print("\n✅ 各年热值与吨发比均在合理带内")


def seed_template_hashes(conn: sqlite3.Connection) -> None:
    """用 2010 年（已确认模板回声年）的 12 个文件生成逐月指纹，存 meta。"""
    import hashlib
    from openpyxl import load_workbook
    have = {r[0] for r in conn.execute(
        "SELECT key FROM meta WHERE key LIKE 'template_hash_%'")}
    if len(have) >= 12:
        return
    n = 0
    for m in range(1, 13):
        p = RAW / f"dailyReport01_2010-{m:02d}.xlsx"
        if not p.exists():
            continue
        wb = load_workbook(p, data_only=True)
        if "一期" not in wb.sheetnames:
            continue
        df = parse_phase_sheet(wb["一期"], 2010, m, "p1")
        h = hashlib.md5(",".join(f"{v:.3f}" for v in df["plant_q"].fillna(0)).encode()).hexdigest()
        conn.execute("INSERT OR REPLACE INTO meta VALUES (?,?)",
                     (f"template_hash_{m:02d}", h))
        n += 1
    conn.commit()
    print(f"模板指纹已生成（{n}/12 月）")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2005-01")
    ap.add_argument("--end", default="2023-12")
    args = ap.parse_args()
    s = tuple(map(int, args.start.split("-")))
    e = tuple(map(int, args.end.split("-")))

    conn = connect()
    print(f"DB: {DB}")
    seed_template_hashes(conn)
    print("① 解析本地已有月度文件…")
    backfill_local(conn)
    print(f"② HTTP 回填 {args.start} ~ {args.end}（间隔 {SLEEP_SECS}s）…")
    backfill_http(conn, s, e)
    n_days = conn.execute("SELECT COUNT(DISTINCT date) FROM daily").fetchone()[0]
    n_rows = conn.execute("SELECT COUNT(*) FROM daily").fetchone()[0]
    print(f"\n③ 入库完成：{n_rows} 行（{n_days} 个日期 × 两期）")
    qc(conn)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
