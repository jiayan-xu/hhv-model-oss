"""把最新一条含 b64 的 CDP Runtime.evaluate 结果写成 dailyReport01_YYYY-MM.xlsx。"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

LOG = Path(__file__).resolve().parents[1] / "data" / "snmis_raw" / "_cdp_log"
OUT = Path(__file__).resolve().parents[1] / "data" / "snmis_raw"


def latest_cdp() -> Path:
    if len(sys.argv) > 1:
        return Path(sys.argv[1])
    files = sorted(LOG.glob("cdp-response-Runtime.evaluate-*.json"))
    if not files:
        raise SystemExit("no cdp dumps")
    return files[-1]


def unwrap(data):
    v = data
    for _ in range(6):
        if isinstance(v, dict) and "b64" in v:
            return v
        if isinstance(v, dict) and "result" in v:
            v = v["result"]
            continue
        if isinstance(v, dict) and "value" in v:
            v = v["value"]
            continue
        break
    raise SystemExit(f"no b64 in {type(v)}")


def main() -> int:
    p = latest_cdp()
    v = unwrap(json.loads(p.read_text(encoding="utf-8")))
    OUT.mkdir(parents=True, exist_ok=True)
    y, m = int(v["y"]), int(v["m"])
    dest = OUT / f"dailyReport01_{y}-{m:02d}.xlsx"
    # 分块 btoa 会在中间插入 '='，先去掉再补齐
    raw = "".join(ch for ch in v["b64"] if ch.isalnum() or ch in "+/")
    raw += "=" * ((4 - len(raw) % 4) % 4)
    dest.write_bytes(base64.b64decode(raw, validate=False))
    print(f"{p.name} -> {dest.name} {dest.stat().st_size} y={y} m={m} len={v.get('len')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
