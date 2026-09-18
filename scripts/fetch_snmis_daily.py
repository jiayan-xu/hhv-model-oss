"""按月下载生产日报一（FineReport，无需登录）。一次一个月，不扫接口。"""
from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlencode

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _local_env import snmis_report_server  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "snmis_raw"
FR = snmis_report_server()


def fetch_month(year: int, month: int) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    dest = OUT / f"dailyReport01_{year}-{month:02d}.xlsx"
    params = {
        "reportlet": "gdmis/dailyReport/dailyReport01.cpt",
        "op": "excel_export",
        "format": "excel",
        "extype": "simple",
        "yearNumss": str(year),
        "monthNumss": str(month),
    }
    print(f"GET {year}-{month:02d}")
    r = requests.get(FR, params=params, timeout=90)
    r.raise_for_status()
    if len(r.content) < 8000:
        print(f"  skip small {len(r.content)} B")
        return dest
    dest.write_bytes(r.content)
    print(f"  {dest.name} {len(r.content)/1024:.1f} KB")
    return dest


def main(args: list[str]) -> int:
    # 默认：2026-01 ~ 2026-08（8 个请求）
    months = []
    if len(args) >= 2:
        y1, m1 = map(int, args[0].split("-"))
        y2, m2 = map(int, args[1].split("-"))
        y, m = y1, m1
        while (y, m) <= (y2, m2):
            months.append((y, m))
            m += 1
            if m > 12:
                y, m = y + 1, 1
    else:
        months = [(2026, m) for m in range(1, 9)]
    for y, m in months:
        fetch_month(y, m)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
