# -*- coding: utf-8 -*-
"""下载地磅日明细（一期/二期×货名）。FineReport VIEWYMDDATE 换日有效，无需登录。"""
from __future__ import annotations

import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _local_env import snmis_report_server  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "snmis_raw" / "gdetail"
FR = snmis_report_server()
REPORTLET = "gdmis/trade/report/garbageDayDetailSView.cpt"


def fetch_day(sess: requests.Session, d: date) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    dest = OUT / f"gdetail_{d.isoformat()}.xlsx"
    if dest.exists() and dest.stat().st_size > 8000:
        return dest
    params = {
        "reportlet": REPORTLET,
        "op": "export",
        "format": "excel",
        "extype": "simple",
        "VIEWYMDDATE": d.isoformat(),
        "orgId": "93",
    }
    r = sess.get(FR, params=params, timeout=90)
    r.raise_for_status()
    if "html" in (r.headers.get("content-type") or "") or len(r.content) < 4000:
        raise RuntimeError(f"{d} not xlsx ({len(r.content)} B {r.headers.get('content-type')})")
    dest.write_bytes(r.content)
    return dest


def daterange(a: date, b: date):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


def main(args: list[str]) -> int:
    if len(args) >= 2:
        a, b = date.fromisoformat(args[0]), date.fromisoformat(args[1])
    else:
        a, b = date(2026, 2, 1), date(2026, 9, 1)
    sess = requests.Session()
    ok = skip = 0
    for d in daterange(a, b):
        dest = OUT / f"gdetail_{d.isoformat()}.xlsx"
        if dest.exists() and dest.stat().st_size > 8000:
            skip += 1
            continue
        fetch_day(sess, d)
        ok += 1
        if ok % 20 == 0:
            print("wrote", ok, "skip", skip, "last", d)
        time.sleep(0.15)
    print("done wrote", ok, "skip", skip, "range", a, b)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
