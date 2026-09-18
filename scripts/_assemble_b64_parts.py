"""从 CDP JSON 里抽出 p0/p1/... 片段拼成 xlsx。"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data" / "snmis_raw"


def unwrap(data):
    v = data
    for _ in range(8):
        if isinstance(v, dict) and ("p0" in v or "b64" in v or "parts" in v):
            return v
        if isinstance(v, dict) and "result" in v:
            v = v["result"]
            continue
        if isinstance(v, dict) and "value" in v:
            v = v["value"]
            continue
        break
    raise SystemExit(f"no parts in {type(v)}")


def main() -> int:
    parts: dict[int, str] = {}
    meta = {}
    for arg in sys.argv[1:]:
        v = unwrap(json.loads(Path(arg).read_text(encoding="utf-8")))
        meta.update({k: v[k] for k in ("y", "m", "len", "b64len", "n") if k in v})
        for k, val in v.items():
            if k.startswith("p") and k[1:].isdigit():
                parts[int(k[1:])] = val
    if not parts:
        raise SystemExit("no pN keys")
    ordered = [parts[i] for i in range(max(parts) + 1) if i in parts]
    missing = [i for i in range(max(parts) + 1) if i not in parts]
    raw = "".join(ordered)
    raw += "=" * ((4 - len(raw) % 4) % 4)
    data = base64.b64decode(raw, validate=False)
    y, m = int(meta["y"]), int(meta["m"])
    dest = OUT / f"dailyReport01_{y}-{m:02d}.xlsx"
    dest.write_bytes(data)
    print(
        f"wrote {dest.name} bytes={len(data)} expected={meta.get('len')} "
        f"b64={len(raw)} expected_b64={meta.get('b64len')} "
        f"parts={sorted(parts)} missing={missing}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
