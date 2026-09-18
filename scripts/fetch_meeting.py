# -*- coding: utf-8 -*-
"""下载生产运行指标日报（dateStr1 换日有效，无需登录）。只导出，不提交。"""
from __future__ import annotations

import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _local_env import snmis_report_server  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "snmis_raw"
FR = snmis_report_server()
CPT = {
    "p1": "gdmis/proMeetingHalf.cpt",
    "p2": "gdmis/proSecondMeetingHalf.cpt",
}


def fetch_one(sess: requests.Session, phase: str, d: date) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    dest = OUT / f"meet_{phase}_{d.isoformat()}.xlsx"
    if dest.exists() and dest.stat().st_size > 8000:
        return dest
    compact = d.strftime("%Y%m%d")
    r = sess.get(
        FR,
        params={
            "reportlet": CPT[phase],
            "op": "export",
            "format": "excel",
            "extype": "simple",
            "dateStr1": compact,
        },
        timeout=90,
    )
    r.raise_for_status()
    if "html" in (r.headers.get("content-type") or "") or len(r.content) < 4000:
        raise RuntimeError(f"{phase} {d} not xlsx ({len(r.content)} B)")
    dest.write_bytes(r.content)
    return dest


def daterange(a: date, b: date):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


def main(args: list[str]) -> int:
    phase = args[0] if args else "p1"
    if phase not in CPT:
        raise SystemExit("phase p1|p2")
    if len(args) >= 3:
        a, b = date.fromisoformat(args[1]), date.fromisoformat(args[2])
    else:
        a, b = date(2025, 1, 1), date(2025, 12, 31)
    sess = requests.Session()
    ok = skip = 0
    for d in daterange(a, b):
        dest = OUT / f"meet_{phase}_{d.isoformat()}.xlsx"
        if dest.exists() and dest.stat().st_size > 8000:
            skip += 1
            continue
        fetch_one(sess, phase, d)
        ok += 1
        if ok % 20 == 0:
            print("wrote", ok, "skip", skip, "last", d, flush=True)
        time.sleep(0.12)
    print("done", phase, "wrote", ok, "skip", skip, "range", a, b, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
