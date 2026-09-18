"""SNMIS DCS 小时曲线过夜批量拉取器（二期 3 炉 × 6 测点 × 2024-2026）。

用法：python scripts/pull_dcs_hourly.py            # 全量（Batch1 2025-02~2026-09 优先，再 Batch2 2024）
      python scripts/pull_dcs_hourly.py --max N    # 限拉 N 个请求（试跑）
      python scripts/pull_dcs_hourly.py --days 8   # 周更：库内最新日−3 到今天
      python scripts/pull_dcs_hourly.py --since 2026-09-01

设计：
- 会话：手工 JSESSIONID Cookie（data/snmis_history.db 同目录 session 文件），连续请求保活；
  响应若为登录页 HTML 判定会话过期 → 优雅停止（进度可续）。
- 断点续传：pull_progress 表记录每个 (code,start,end) 状态，重跑自动跳过。
- 存储：snmis_history.db
    hourly_hourly 表：逐小时聚合（code/desc/day/hour/mean/min/max/n）
    raw_points 表：整窗分钟点 zlib 压缩 BLOB（可恢复原始分辨率）
- 纪律：12h 窗、单点、间隔 1.5s（总节奏约 2.3s/请求，全量约 13 小时）。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import zlib
from datetime import date, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _local_env import require_snmis_base, session_file, snmis_login_base  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "snmis_history.db"
SESS = session_file()
BASE = snmis_login_base()
ENDPOINT = f"{BASE}/assessmentStatistics/listDcsParametersCharts.do"
SLEEP = 1.5

FURN = {
    "f4": {
        "主汽流量": "V4.DPU1002.SH0129.AALM01.PV",
        "主汽压力": "V4.DPU1002.HW.AI020304.PV",
        "给水温度": "V4.DPU1002.HW.RT030101.PV",
        "炉膛温度": "V4.DPU1001.SH0004.AALM01.PV",
        "LHV": "V4.DPU1001.SH0212.PRO2.IN",
        "省煤器氧": "V4.DPU1001.HW.AI090401.PV",
    },
    "f5": {
        "主汽流量": "V4.DPU1003.SH0129.AALM01.PV",
        "主汽压力": "V4.DPU1004.HW.AI020304.PV",
        "给水温度": "V4.DPU1004.HW.RT030101.PV",
        "炉膛温度": "V4.DPU1003.SH0004.AALM01.PV",
        "LHV": "V4.DPU1003.SH0212.PRO2.IN",
        "省煤器氧": "V4.DPU1003.HW.AI090401.PV",
    },
    "f6": {
        "主汽流量": "V4.DPU1005.SH0129.AALM01.PV",
        "主汽压力": "V4.DPU1006.HW.AI020304.PV",
        "给水温度": "V4.DPU1006.HW.RT030101.PV",
        "炉膛温度": "V4.DPU1005.SH0004.AALM01.PV",
        "LHV": "V4.DPU1005.SH0212.PRO2.IN",
        "省煤器氧": "V4.DPU1005.HW.AI090401.PV",
    },
}
BATCH1 = (date(2025, 2, 1), date(2026, 9, 1))   # 反推窗口，优先
BATCH2 = (date(2024, 1, 1), date(2024, 12, 31))  # 二期纯烧基线

DDL = """
CREATE TABLE IF NOT EXISTS hourly_hourly (
  code TEXT, desc TEXT, furnace TEXT, point TEXT,
  day TEXT, hour INTEGER, mean REAL, vmin REAL, vmax REAL, n INTEGER,
  PRIMARY KEY (code, day, hour));
CREATE TABLE IF NOT EXISTS raw_points (
  code TEXT, start TEXT, end TEXT, n INTEGER, blob BLOB, fetched_at TEXT,
  PRIMARY KEY (code, start));
CREATE TABLE IF NOT EXISTS pull_progress (
  jid TEXT PRIMARY KEY, status TEXT, n INTEGER, mean REAL, at TEXT);
"""


def daterange(a: date, b: date):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


def build_jobs() -> list[tuple[str, str, str, str, str, str]]:
    """按价值排序的作业清单：(furnace, point, desc, code, start, end)。"""
    jobs = []
    for b1 in (True, False):
        a, b = BATCH1 if b1 else BATCH2
        # 主汽流量最优先（三炉），其余测点随后
        for point in ("主汽流量", "省煤器氧", "给水温度", "炉膛温度", "主汽压力", "LHV"):
            for f, pts in FURN.items():
                for d in daterange(a, b):
                    for (st, en) in ((f"{d} 00:00:00", f"{d} 12:00:00"),
                                     (f"{d} 12:00:00", f"{d} 23:59:00")):
                        jobs.append((f, point, pts[point], st, en))
    return jobs


def build_jobs_range(a: date, b: date) -> list[tuple[str, str, str, str, str, str]]:
    """指定日期范围的作业清单（周更/补拉用）。"""
    jobs = []
    for point in ("主汽流量", "省煤器氧", "给水温度", "炉膛温度", "主汽压力", "LHV"):
        for f, pts in FURN.items():
            for d in daterange(a, b):
                for (st, en) in ((f"{d} 00:00:00", f"{d} 12:00:00"),
                                 (f"{d} 12:00:00", f"{d} 23:59:00")):
                    jobs.append((f, point, pts[point], st, en))
    return jobs


def latest_db_day(conn: sqlite3.Connection) -> date | None:
    row = conn.execute("SELECT MAX(day) FROM hourly_hourly").fetchone()
    if not row or not row[0]:
        return None
    try:
        return date.fromisoformat(str(row[0])[:10])
    except ValueError:
        return None


class Puller:
    def __init__(self):
        self.sess = json.loads(SESS.read_text(encoding="utf-8"))
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": "Mozilla/5.0 Chrome/120.0",
            "Cookie": f"JSESSIONID={self.sess['jSidVar']}",
        })
        self.expired = False

    def fetch(self, code: str, st: str, en: str):
        r = self.s.post(ENDPOINT, data={"codes": code, "startTime": st, "endTime": en}, timeout=45)
        try:
            j = r.json()
        except ValueError:
            self.expired = True   # 非 JSON 响应 = 会话过期/登录墙
            return None
        if "toLoginPage" in r.text[:2000] or ("data" not in j):
            self.expired = True
            return None
        d = (j.get("data") or [{}])[0]
        return d.get("singleChartsDto") or []


def init_db() -> sqlite3.Connection:
    sys.path.insert(0, str(ROOT))
    from hhv import db as _db
    conn = _db.connect(DB)   # WAL + busy_timeout + 索引（写库期间跑批读库不互锁）
    conn.executescript(DDL)
    _db.ensure_indexes(conn)
    return conn


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=0)
    ap.add_argument("--days", type=int, default=0,
                    help="增量：从库内最新日往前 overlap 天拉到今天（周更推荐 --days 8）")
    ap.add_argument("--overlap", type=int, default=3,
                    help="增量时从最新日回溯的天数，默认 3（补不完整日）")
    ap.add_argument("--since", type=str, default="",
                    help="增量：从该日 YYYY-MM-DD 拉到今天")
    args = ap.parse_args()

    conn = init_db()
    puller = Puller()
    if args.since:
        a = date.fromisoformat(args.since)
        b = date.today()
        jobs = build_jobs_range(a, b)
        print(f"增量模式 --since {a} ~ {b}", flush=True)
    elif args.days > 0:
        latest = latest_db_day(conn)
        if latest is None:
            a = date.today() - timedelta(days=args.days)
        else:
            a = latest - timedelta(days=max(args.overlap, 0))
        b = date.today()
        if a > b:
            a = b
        jobs = build_jobs_range(a, b)
        print(f"增量模式：库内最新 {latest or '无'} → 从 {a} 拉到 {b}"
              f"（overlap={args.overlap}）", flush=True)
    else:
        jobs = build_jobs()
    done = {r[0] for r in conn.execute("SELECT jid FROM pull_progress WHERE status IN ('ok','empty')")}
    todo = [j for j in jobs if f"{j[0]}|{j[1]}|{j[3]}" not in done]
    print(f"作业总数 {len(jobs)}，已完成 {len(done)}，待拉 {len(todo)}", flush=True)

    count = 0
    for (f, point, code, st, en) in todo:
        if args.max and count >= args.max:
            print(f"--max {args.max} 达到，停止", flush=True)
            break
        if puller.expired:
            print("!! 会话过期——优雅停止，进度已保存，重启脚本可续", flush=True)
            break
        jid = f"{f}|{point}|{st}"
        vals: list[float] = []
        npts = 0
        try:
            pts = puller.fetch(code, st, en)
            if pts is None:
                break
            npts = len(pts)
            hours = {}
            for p in pts:
                t = str(p.get("name", ""))
                v = p.get("nameValue")
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    continue
                vals.append(v)
                h = int(t[11:13]) if len(t) >= 13 else 0
                m0, m1, nh, sm = hours.get(h, (None, None, 0, 0.0))
                hours[h] = (v if m0 is None else min(m0, v), v if m1 is None else max(m1, v),
                            nh + 1, sm + v)
            blob = zlib.compress(json.dumps(pts, ensure_ascii=False).encode("utf-8"), 6)
            conn.execute("INSERT OR REPLACE INTO raw_points VALUES (?,?,?,?,?,datetime('now','localtime'))",
                         (code, st, en, len(pts), blob))
            for h, (mn, mx, nh, sm) in hours.items():
                day = st[:10]
                conn.execute("INSERT OR REPLACE INTO hourly_hourly VALUES (?,?,?,?,?,?,?,?,?,?)",
                             (code, FURN[f][point], f, point, day, h,
                              sm / nh if nh else None, mn, mx, nh))
            # 空窗（该测点此段确实没有数据，如 f5/f6 主汽流量厂里未留存）记 empty：
            # 拉取本身成功，既不算失败也无需重拉（done 集合含 empty）。
            conn.execute("INSERT OR REPLACE INTO pull_progress VALUES (?,?,?,?,datetime('now','localtime'))",
                         (jid, "ok" if hours else "empty", npts, None))
            conn.commit()
        except Exception as e:
            conn.execute("INSERT OR REPLACE INTO pull_progress VALUES (?,?,?,?,datetime('now','localtime'))",
                         (jid, "error: " + str(e)[:120], 0, None))
            conn.commit()
            print(f"ERR {jid}: {str(e)[:80]}", flush=True)
        # 进度打印放在 try 外：此前它在 try 内且对空窗除零，
        # 异常会把已提交的 ok/empty 覆盖成 error，导致这些窗口每次重跑都重拉、永远失败。
        count += 1
        if count % 50 == 0:
            nz = sum(1 for v in vals if v != 0)
            avg = (sum(vals) / len(vals)) if vals else 0.0
            print(f"[{count}/{len(todo)}] {f} {point} {st[:10]} "
                  f"n={npts} avg={avg:.1f} nz={nz}", flush=True)
        time.sleep(SLEEP)

    print(f"\n完成 {count} 个请求；会话过期={puller.expired}", flush=True)
    print(conn.execute("SELECT status, COUNT(*) FROM pull_progress GROUP BY status").fetchall(), flush=True)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
