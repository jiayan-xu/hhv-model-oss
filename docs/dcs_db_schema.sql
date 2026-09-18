-- DCS 小时库脱敏 DDL（与 docs/dcs_db_schema.md 一致）
-- 用法示例：sqlite3 data/snmis_history.db < docs/dcs_db_schema.sql

CREATE TABLE IF NOT EXISTS hourly_hourly (
  code TEXT, desc TEXT, furnace TEXT, point TEXT,
  day TEXT, hour INTEGER, mean REAL, vmin REAL, vmax REAL, n INTEGER,
  PRIMARY KEY (code, day, hour)
);

CREATE TABLE IF NOT EXISTS raw_points (
  code TEXT, start TEXT, end TEXT, n INTEGER, blob BLOB, fetched_at TEXT,
  PRIMARY KEY (code, start)
);

CREATE TABLE IF NOT EXISTS pull_progress (
  jid TEXT PRIMARY KEY, status TEXT, n INTEGER, mean REAL, at TEXT
);

CREATE INDEX IF NOT EXISTS idx_hh_point_day ON hourly_hourly(point, day);
CREATE INDEX IF NOT EXISTS idx_hh_furnace_day ON hourly_hourly(furnace, day);
