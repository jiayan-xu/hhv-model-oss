# DCS 小时库脱敏字典（SQLite）

本文描述 `hhv-model` 消费侧约定的 **DCS 小时数据 SQLite 结构**，便于他人自建同构库后接入反推流水线。

> **脱敏说明**  
> - 不含厂内 DCS/PLC 点位地址、主机名、账号。  
> - 不含真实运行数据；示例行为合成值。  
> - 库文件默认路径 `data/snmis_history.db`，**不入库**（见 `.gitignore`）。  
> - 模型入口：`hhv/dcs.py`（读小时序）与 `hhv/calib.py`（`point='LHV'` 效率互证闸门）。

---

## 1. 文件与连接

| 项 | 约定 |
|---|---|
| 默认库路径 | `data/snmis_history.db`（可用 `config.yaml` → `paths.dcs_hourly_db` 覆盖） |
| 引擎 | SQLite 3 |
| 连接方式 | 统一经 `hhv.db.connect()`：`journal_mode=WAL`，`busy_timeout=30000` |
| 配置开关 | `config` 中 `dcs.enabled: true` 时，二期时序可从本库组装 |

未配置库路径或库中无表时，效率闸门返回 **SKIP**，主流程仍可用纯 CSV 时序跑通。

---

## 2. 表结构

### 2.1 `hourly_hourly` — 逐小时聚合（模型主读表）

```sql
CREATE TABLE IF NOT EXISTS hourly_hourly (
  code    TEXT,     -- 源系统点位标识（各厂自定，勿提交真实值）
  desc    TEXT,     -- 点位描述（可选；可与 code 相同）
  furnace TEXT,     -- 锅炉/机组逻辑名，如 f4/f5/f6（可自定义）
  point   TEXT,     -- 测点业务名，见第 3 节字典
  day     TEXT,     -- 日期 YYYY-MM-DD
  hour    INTEGER,  -- 小时 0–23（本地时）
  mean    REAL,     -- 该小时算术平均（模型主要用此列）
  vmin    REAL,     -- 小时内最小值（可选）
  vmax    REAL,     -- 小时内最大值（可选）
  n       INTEGER,  -- 聚合样本点数（可选）
  PRIMARY KEY (code, day, hour)
);
```

**推荐索引**（`hhv.db.INDEXES`，连接时自动 `CREATE INDEX IF NOT EXISTS`）：

```sql
CREATE INDEX IF NOT EXISTS idx_hh_point_day   ON hourly_hourly(point, day);
CREATE INDEX IF NOT EXISTS idx_hh_furnace_day ON hourly_hourly(furnace, day);
```

### 2.2 `raw_points` — 可选：拉数侧原始点缓冲

```sql
CREATE TABLE IF NOT EXISTS raw_points (
  code       TEXT,   -- 同 hourly_hourly.code
  start      TEXT,   -- 窗起点（含），如 'YYYY-MM-DD HH:MM:SS'
  end        TEXT,   -- 窗终点
  n          INTEGER,
  blob       BLOB,   -- 厂内实现常用 zlib 压缩的分钟序列；模型不依赖此表
  fetched_at TEXT,
  PRIMARY KEY (code, start)
);
```

### 2.3 `pull_progress` — 可选：拉数断点续传

```sql
CREATE TABLE IF NOT EXISTS pull_progress (
  jid     TEXT PRIMARY KEY,  -- 如 '{furnace}|{point}|{start}'
  status  TEXT,              -- ok / empty / error 等
  n       INTEGER,
  mean    REAL,
  at      TEXT
);
```

模型跑批**只强制依赖** `hourly_hourly`；后两表是拉数工具的运维表。

---

## 3. 测点业务字典（`point` 列）

`hhv/dcs.py` 的 `POINT_STD` 将业务名映射到模型标准列。自建库时请用**相同中文 `point` 值**（或改 `POINT_STD`）：

| `point`（入库值） | 模型标准字段 | 单位（约定） | 用途 |
|---|---|---|---|
| 主汽流量 | `steam_flow` | t/h | 形状曲线；缺测时用日报日均×形状 |
| 主汽压力 | `steam_p` | MPa（表压，配置 `units.steam_p`） | 运行炉判停 + 反平衡 |
| 给水温度 | `feedwater_t` | ℃ | 反平衡 |
| 省煤器氧 | `o2` | %（干基） | 过量空气 / q2 |
| 炉膛温度 | `furnace_t` | ℃ | 与压力一起判断该炉是否在烧 |
| LHV | （闸门用，不进 `POINT_STD`） | 模型侧按 MJ/kg 量级过滤 | 仅 `calib` 效率互证：`point='LHV'` 且 `0 < mean < 20` 按日聚合 |

未写入库、且 CSV 日报已有的字段：**主汽温度** `steam_t`、**排烟温度** `fluegas_t` — 由日报列回填（`dcs.py` 注释约定）。

**炉名**（`furnace`）：演示与参考实现用 `f4`/`f5`/`f6` 表示二期三炉；可改为任意标签，与 `config.dcs.furnaces`、`flow_shape_furnace` 一致即可。

---

## 4. 模型如何读库

### 4.1 二期小时组装（`hhv.dcs`）

1. `SELECT day, hour, furnace, point, mean FROM hourly_hourly`  
   （`day` 在 `[start,end]`，`furnace IN (cfg)`，`mean IS NOT NULL`）  
2. 拼 `time = day + hour 小时` → 长表  
3. `point` → `POINT_STD` 后透视为 `(std, furnace)` 宽表  
4. **开停**：`furnace_t >= on_furnace_t` **或** `steam_p >= on_steam_p`（缺测视为 False）  
5. 压力 / 给水 / 氧：仅对**运行炉**做小时算术均  
6. **蒸汽流量**：日报日均总量 × `flow_shape_furnace` 小时形状（形状炉 `mean` 归一）；不编造缺失炉的绝对流量  
7. `steam_t` / `fluegas_t`：无 DCS 时用日报列  

### 4.2 效率互证闸门（`hhv.calib.eta_implied_gate_from_db`）

```sql
SELECT day, AVG(mean) AS lhv
FROM hourly_hourly
WHERE point = 'LHV' AND mean > 0 AND mean < 20
GROUP BY day;
```

与蒸汽侧日均效率按日对齐比较；库缺失/无 LHV 时闸门 **SKIP**，不阻断主回归。

---

## 5. 合成示例（自建库用）

```sql
-- 合成数据，仅演示表结构
INSERT INTO hourly_hourly
  (code, desc, furnace, point, day, hour, mean, vmin, vmax, n)
VALUES
  ('demo_p', 'demo', 'f4', '主汽压力', '2026-01-06', 10, 3.85, 3.80, 3.90, 60),
  ('demo_t', 'demo', 'f4', '炉膛温度', '2026-01-06', 10, 850.0, 840.0, 860.0, 60),
  ('demo_f', 'demo', 'f4', '主汽流量', '2026-01-06', 10, 55.0, 50.0, 58.0, 60),
  ('demo_o', 'demo', 'f4', '省煤器氧', '2026-01-06', 10, 4.2, 3.9, 4.5, 60),
  ('demo_w', 'demo', 'f4', '给水温度', '2026-01-06', 10, 132.0, 130.0, 134.0, 60),
  ('demo_l', 'demo', 'f4', 'LHV',      '2026-01-06', 10, 8.5, 8.0, 9.0, 60);
```

建库脚本可直接执行第 2 节 DDL + `hhv.db.ensure_indexes`。

---

## 6. 相关代码索引

| 模块 | 作用 |
|---|---|
| `hhv/db.py` | 连接、WAL、索引 |
| `hhv/dcs.py` | `hourly_hourly` → 二期小时标准列 |
| `hhv/calib.py` | `LHV` 日均 → 效率互证闸门 |
| `config*.yaml` → `dcs.*` | 炉列表、形状炉、开停阈值、`paths.dcs_hourly_db` |

拉数工具与厂内点位映射在**私有部署**中维护，不在本开源仓。
