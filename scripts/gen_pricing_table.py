#!/usr/bin/env python
"""从 data/pricing_calibration.yaml 生成 PFAiX 定价页的 OP 表（单一来源，杜绝口径漂移）。

背景：定价效率口径曾有三份拷贝（Python OP_TABLE、前端内联表、已废弃的 pricing-calc.js），
2026-09-08 审计发现 Excel 口径 78~81% 与页面 86.3~86.6% 不一致。现在校准值只写
data/pricing_calibration.yaml，前端表由本脚本生成。

用法：
    python scripts/gen_pricing_table.py                 # 写入 ~/jan/shell-dist/model.html
    python scripts/gen_pricing_table.py --check         # 只校验是否漂移，漂移退出码 1
    python scripts/gen_pricing_table.py --model-html <路径>
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hhv.pricing import CALIBRATION_PATH, OP_TABLE, load_pricing_calibration  # noqa: E402

BEGIN = "  /* OP_TABLE:BEGIN"
END = "  /* OP_TABLE:END */"
DEFAULT_HTML = pathlib.Path.home() / "jan" / "shell-dist" / "model.html"

# JS 行结构：[blend, eta, aux, steam, h_s, h_fw, scr, limeKg, acKg, amKg, ngasM3]
# 前 3 列取校准值（缺失则回退 OP_TABLE），其余沿用 OP_TABLE。
NUM_FMT = {0: "{:g}", 1: "{:.1f}", 2: "{:.1f}", 3: "{:.2f}", 4: "{:g}", 5: "{:g}",
           6: "{:g}", 7: "{:.1f}", 8: "{:.2f}", 9: "{:.2f}", 10: "{:.2f}"}


def render_block(calibration: dict[int, dict], source: str = "", date: str = "",
                 decl: str = "var", lime_by_blend: dict[int, float] | None = None) -> str:
    lines = [
        f"{BEGIN} —— 由 hhv-model/scripts/gen_pricing_table.py 从 data/pricing_calibration.yaml 生成，勿手改；",
        "     改口径请改校准文件后重跑该脚本（--check 可校验是否漂移）"
        + (f"；依据：{source}（{date}）" if source else "") + " */",
        f"  {decl} OP = [",
    ]
    rows = []
    for row in OP_TABLE:
        vals = list(row)
        ov = calibration.get(int(row[0]), {})
        for i, key in enumerate(("eta", "aux", "steam_rate")):
            if key in ov:
                vals[i + 1] = ov[key]
        if lime_by_blend is not None:
            lime = lime_by_blend.get(int(row[0]))
            if lime is not None:
                vals[7] = float(lime)
        rows.append("    [" + ", ".join(NUM_FMT[i].format(v) for i, v in enumerate(vals)) + "]")
    lines.append(",\n".join(rows))
    lines.append("  ];")
    lines.append(END)
    return "\n".join(lines)


def extract_current(html: str) -> str | None:
    i = html.find(BEGIN)
    if i < 0:
        return None
    j = html.find(END, i)
    if j < 0:
        return None
    return html[i:j + len(END)]


DEFAULT_INTERACTIVE = pathlib.Path("outputs/config.snmis_daily/固废定价测算_交互.html")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-html", type=pathlib.Path, default=DEFAULT_HTML)
    ap.add_argument("--interactive-html", type=pathlib.Path,
                    default=pathlib.Path(__file__).resolve().parents[1] / DEFAULT_INTERACTIVE)
    ap.add_argument("--check", action="store_true", help="只校验，不写入")
    args = ap.parse_args()

    import yaml
    raw = yaml.safe_load(CALIBRATION_PATH.read_text(encoding="utf-8")) or {}
    calibration = load_pricing_calibration()
    if not calibration:
        print(f"[gen_pricing_table] 校准文件缺失或为空：{CALIBRATION_PATH}")
        return 1
    lime_raw = raw.get("lime_kg_per_t") or {}
    lime_by_blend = {int(k): float(v) for k, v in lime_raw.items()} if lime_raw else None

    targets = [("var", args.model_html), ("const", args.interactive_html)]
    rc = 0
    for decl, path in targets:
        block = render_block(
            calibration, raw.get("source", ""), str(raw.get("date", "")), decl,
            lime_by_blend=lime_by_blend)
        if not path.is_file():
            print(f"[gen_pricing_table] 找不到目标页面：{path}")
            rc = 1
            continue
        html = path.read_text(encoding="utf-8")
        current = extract_current(html)
        if current is None:
            print(f"[gen_pricing_table] {path.name} 里没有 OP_TABLE:BEGIN/END 标记")
            rc = 1
            continue
        if args.check:
            if current.strip() != block.strip():
                print(f"[gen_pricing_table] ✗ {path.name} 的 OP 表与校准文件不一致（口径漂移）：")
                print("--- 页面当前 ---")
                print(current)
                print("--- 应为 ---")
                print(block)
                rc = 1
            continue
        new_html = html.replace(current, block, 1)
        if new_html != html:
            path.write_text(new_html, encoding="utf-8")
            print(f"[gen_pricing_table] 已写入 {path.name}（{len(calibration)} 档，{decl}）")
    if args.check:
        if rc == 0:
            print(f"[gen_pricing_table] ✓ 两处 OP 表均与 {CALIBRATION_PATH.name} 一致（{len(calibration)} 档）")
        return rc
    return rc


if __name__ == "__main__":
    sys.exit(main())
