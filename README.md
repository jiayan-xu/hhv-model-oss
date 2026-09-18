# hhv-model — 掺烧工业固废热值反推模型

基于锅炉运行时序与入厂台账，反推掺烧工业固废的**收到基低位热值**（可分来源），
输出可用于处置定价的点估计与 Bootstrap 置信区间。

方法概要：蒸汽侧正平衡 → 日/周自动筛选 → 一期同期锚定 → k 因子交叉验证闸门
→ Huber 稳健回归分解 → Bootstrap CI。

> **开源说明**：本仓库**不含**任何电厂真实运行数据、化验报告、定价实测参数或内网系统地址。
> 演示流水线仅使用合成数据（`data/demo/`）。接真实数据请复制 `config.example.yaml`，
> 将路径指向你自己的 CSV；接生产系统前请配置 `.env`（见 `.env.example`）。

## 快速开始（合成数据）

```bash
pip install -r requirements.txt
python scripts/make_demo_data.py
python run.py --config config.demo.yaml
# 查看 outputs/<run_name>/report.md 与 figures/
```

可选展示脚本（仍基于 demo/本地 CSV，不依赖内网）：

```bash
python scripts/showcase.py
```

## 接入你自己的数据

1. 复制 `config.example.yaml` → `config.yaml`（已 gitignore）。
2. 按 `columns` / `ledger_columns` 映射你的列名（中文列名可直接填）。
3. 三组时序（一期当期 / 二期当期 / 二期历史，若无历史则闸门降级）+ 一份按日台账。
4. 运行：`python run.py --config config.yaml`

定价引擎若要使用**本厂实测效率校准**，将 `data/pricing_calibration.example.yaml`
复制为 `data/pricing_calibration.yaml` 后填写；文件不存在时引擎回退内置 OP 表。

## 生产系统拉数（可选）

本仓库**不包含**厂内 SNMIS/DCS 拉数与月度报告导出脚本（含账号、报表口径与企业数据）。
若你方有内网报表系统，请在私有仓库自行维护拉数脚本，并将凭据放在 `.env`
（参考 `.env.example`：`SNMIS_BASE_URL`、`HHV_SECRETS_DIR`、`DASHBOARD_DB`）。

核心库只负责：读本地 CSV/台账 → 反推热值 → 闸门 → 报告/定价。

## 输出结构（`outputs/<run_name>/`）

| 文件 | 说明 |
|---|---|
| `report.md` | 筛选统计、效率、k 闸门、分来源热值 ± CI、诊断 |
| `weekly_series.csv` | 周尺度热值/质量/α |
| `figures/*.png` | 周序列、回归拟合、筛选覆盖、k 闸门 |

## 结果口径

- 生活垃圾热值 Q_生活：一期锚定或回归截距，kJ/kg 收到基低位
- 工业固废热值 Q_j：分来源回归 + Bootstrap 95% CI
- k 闸门：&lt;5% 主锚可用；5%–10% 双锚并报；&gt;10% 先查运行变更

## 定价引擎

`hhv/pricing.py` 将市场化处置测算表引擎化，支持掺烧分档、石灰单耗、
热值不确定性抽样与报价分档。演示/引擎校验可用：

```bash
python scripts/validate_pricing.py
```

厂内实测效率校准请复制 `data/pricing_calibration.example.yaml` 后本机填写；
真实炉渣出厂量等放在本机 `data/plant/`（已 gitignore），**不要提交到公开仓库**。

## 合成数据验证（可复现）

演示条件：Q_生活=6396（含季节漂移）、废布料=12000、污泥=4500、表计 +0.6%、
效率系统差约 5%、含启停/低负荷事件日。

| 指标 | 真值 | 反推 | 95% CI | 真值在 CI 内 |
|---|---|---|---|---|
| Q_生活 | 6396 | ~6071（效率系统差） | 6015–6125 | —（故意设置的系统差） |
| Q_废布料 | 12000 | ~11937 | 11693–12217 | ✓ |
| Q_污泥 | 4500 | ~4283 | 3800–4689 | ✓ |

交叉验证闸门对合成数据应 PASS；分来源增量在同周相减后可部分抵消效率系统差。

## 目录约定

```
hhv/           模型与定价引擎
scripts/       演示与校验（make_demo_data / run 路径 / validate_pricing / showcase）
data/demo/     合成数据（可入库）
config.demo.yaml / config.example.yaml
.env.example   环境变量模板（本机 .env 勿提交）
```

## License

按你方开源策略补充（例如 MIT / Apache-2.0）。未声明前请勿默认视为可任意商用。
