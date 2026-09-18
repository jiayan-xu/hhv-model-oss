"""一键验证：审查者解压审查包后在包根目录执行，复现全部验证声明。

  python scripts/verify_all.py

依次运行：合成数据生成 → 反推全流程 → 定价引擎逐格验证 → 定价报告生成
→（可选）xlsx 质检。任一步失败即停止并返回非零退出码。
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parents[1]
XLSX_VALIDATE = os.environ.get(
    "XLSX_VALIDATE",
    r"C:/Users/user/.zcode/cli/plugins/cache/zcode-plugins-official/document-skills/0.1.4/skills/xlsx/xlsx.py",
)

STEPS = [
    ("① 合成演示数据（含真值）", [sys.executable, "scripts/make_demo_data.py"]),
    ("② 热值反推全流程（期望：闸门 PASS、真值入 CI）", [sys.executable, "run.py", "--config", "config.demo.yaml"]),
    ("③ 定价引擎 vs V4 算例 13 项逐格验证", [sys.executable, "scripts/validate_pricing.py"]),
    ("④ 生成定价建议 Excel（V4 算例对照）",
     [sys.executable, "scripts/build_pricing_report.py", "--v4"]),
]


def main() -> int:
    print("=" * 72)
    print("hhv-model 整合模型 一键验证")
    print(f"包根目录: {ROOT}")
    print("=" * 72)
    for name, cmd in STEPS:
        print(f"\n▶ {name}\n  $ {' '.join(cmd)}")
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        tail = "\n".join((p.stdout or "").strip().splitlines()[-6:])
        if (p.stderr or "").strip():
            tail += "\n[stderr]\n" + "\n".join(p.stderr.strip().splitlines()[-3:])
        print(tail)
        if p.returncode != 0:
            print(f"\n❌ 步骤失败（退出码 {p.returncode}）：{name}")
            return p.returncode or 1
        print(f"✅ {name}")
    # 可选第⑤步：xlsx 质检（依赖本机 xlsx 技能路径；缺失则跳过并注明）
    out = ROOT / "outputs" / "pricing" / "市场化垃圾定价建议_V4.1.xlsx"
    if pathlib.Path(XLSX_VALIDATE).exists() and out.exists():
        p = subprocess.run([sys.executable, XLSX_VALIDATE, "validate", str(out)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        ok = '"status": "passed"' in (p.stdout or "") and '"total_issues": 0' in (p.stdout or "")
        print(f"\n▶ ⑤ xlsx 完整性质检：{'✅ passed / 0 issues' if ok else '❌ ' + (p.stdout or '')[:200]}")
        if not ok:
            return 1
    else:
        print("\n▶ ⑤ xlsx 完整性质检：跳过（本机未检测到 xlsx 技能路径，"
              "可用环境变量 XLSX_VALIDATE 指定；产物仍可用 Excel/WPS 打开核对）")
    print("\n" + "=" * 72)
    print("全部验证通过。审查核对点：步骤②输出中的真值比对表、步骤③的 13 项 diff 表。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
