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

## 生产系统拉数（可选，需自备地址与凭据）

脚本 `scripts/snmis_login.py`、`pull_dcs_hourly.py`、`fetch_*.py` 用于从厂内
SNMIS/DCS 报表系统拉取数据。仓库**不内置**任何主机地址或账号：

```bash
cp .env.example .env
# 编辑 .env：SNMIS_BASE_URL、HHV_SECRETS_DIR、DASHBOARD_DB 等
# 账密放在 .env 指向的 secrets 目录，勿提交到 Git
```

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
热值不确定性抽样与报价分档。合成/演示口径可用：

```bash
python scripts/validate_pricing.py
python scripts/build_pricing_report.py --v4
```

厂内实测校准值与具体报价结论**不在本仓库**，请在本机 `data/plant/` 与
`data/pricing_calibration.yaml` 中自行维护（均已 gitignore）。

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
scripts/       演示、校验、报表；拉数脚本依赖 .env
data/demo/     合成数据（可入库）
data/plant/    真实运行数据（gitignore，本机自备）
data/assays/   化验台账（gitignore，本机自备）
config.demo.yaml / config.example.yaml
.env.example   环境变量模板
```

## License

按你方开源策略补充（例如 MIT / Apache-2.0）。未声明前请勿默认视为可任意商用。
