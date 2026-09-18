# -*- coding: utf-8 -*-
"""本机/密钥路径与业务系统地址：一律读环境变量，禁止写进仓库。

优先级：进程环境变量 → 仓库根 .env（已 gitignore）→ ~/.svc-secrets/dashboard.env
开源克隆若未配置，相关拉数脚本会明确报错，而不是使用内网默认地址。
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, val)


_load_env_file(ROOT / ".env")
_load_env_file(Path.home() / ".svc-secrets" / "dashboard.env")


def snmis_base() -> str:
    """SNMIS/DCS 系统根地址，例如 http://<host>:<port>/snmis（含或不含 /snmis 均可）。"""
    return (os.environ.get("SNMIS_BASE_URL") or "").rstrip("/")


def require_snmis_base() -> str:
    b = snmis_base()
    if not b:
        raise SystemExit(
            "未配置 SNMIS_BASE_URL。请在环境变量或仓库根 .env（勿提交）中设置，"
            "例如 SNMIS_BASE_URL=http://<host>:<port>/snmis"
        )
    return b


def snmis_login_base() -> str:
    """登录/Spring 接口前缀（以 /snmis 结尾）。"""
    b = require_snmis_base()
    return b if b.endswith("/snmis") else b + "/snmis"


def snmis_report_server() -> str:
    return snmis_login_base() + "/ReportServer"


def secrets_dir() -> Path:
    return Path(os.environ.get("HHV_SECRETS_DIR") or (Path.home() / ".svc-secrets"))


def session_file() -> Path:
    return Path(
        os.environ.get("SNMIS_SESSION_FILE")
        or (secrets_dir() / "snmis_pull_session.json")
    )


def credentials_env() -> Path:
    return Path(
        os.environ.get("HHV_CREDENTIALS_ENV")
        or (secrets_dir() / "dashboard.env")
    )


def dashboard_db() -> Path:
    p = os.environ.get("DASHBOARD_DB") or ""
    return Path(p) if p else (Path.home() / "dashboard" / "dashboard.db")
