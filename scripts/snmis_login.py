# -*- coding: utf-8 -*-
"""SNMIS 自动登录：写 JSESSIONID 到 snmis_pull_session.json（DCS 周更用）。

流程（与 loginNew.js 一致）：
  GET  /snmis/authen/toLoginPage.do
  POST /snmis/rasLoginMgmt/obtainEncryptedParameter.do  → RSA 公钥
  RSA PKCS1_v1_5 加密密码
  POST /snmis/j_spring_security_check;jsessionid=...  j_username / j_password

凭据与系统地址见 scripts/_local_env.py（SNMIS_BASE_URL / secrets，勿写入仓库）。
用法：python scripts/snmis_login.py
"""
from __future__ import annotations

import base64
import json
import re
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _local_env import credentials_env, session_file, snmis_login_base  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BASE = snmis_login_base()
SESS_OUT = session_file()
ENV = credentials_env()
DCS_PROBE = f"{BASE}/assessmentStatistics/listDcsParametersCharts.do"


def load_creds() -> tuple[str, str]:
    user = pw = ""
    if ENV.is_file():
        for line in ENV.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("SNMIS_USERNAME="):
                user = line.split("=", 1)[1].strip().strip('"').strip("'")
            elif line.startswith("SNMIS_PASSWORD="):
                pw = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not user or not pw:
        raise SystemExit(f"缺少 SNMIS_USERNAME/SNMIS_PASSWORD：{ENV}")
    return user, pw


def rsa_encrypt(pub_pem: str, password: str) -> str:
    from Crypto.Cipher import PKCS1_v1_5
    from Crypto.PublicKey import RSA
    key = RSA.import_key(pub_pem)
    cipher = PKCS1_v1_5.new(key)
    return base64.b64encode(cipher.encrypt(password.encode("utf-8"))).decode("ascii")


def login() -> str:
    user, pw = load_creds()
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 Chrome/120.0"})
    r = s.get(f"{BASE}/authen/toLoginPage.do", timeout=15)
    r.raise_for_status()
    jsid = s.cookies.get("JSESSIONID")
    if not jsid:
        m = re.search(r"jsessionid=([A-F0-9]+)", r.text, re.I)
        jsid = m.group(1) if m else ""
    if not jsid:
        raise SystemExit("登录页未返回 JSESSIONID")
    # RSA 公钥
    rk = s.post(
        f"{BASE}/rasLoginMgmt/obtainEncryptedParameter.do",
        data={},
        timeout=15,
    )
    try:
        j = rk.json()
    except ValueError as e:
        raise SystemExit(f"取公钥失败（非 JSON）：{rk.status_code} {rk.text[:120]}") from e
    if not j.get("success") or not j.get("data"):
        raise SystemExit(f"取公钥失败：{j}")
    pub = str(j["data"])
    if "BEGIN PUBLIC KEY" not in pub:
        pub = (
            "-----BEGIN PUBLIC KEY-----\n"
            + "\n".join(pub[i:i + 64] for i in range(0, len(pub), 64))
            + "\n-----END PUBLIC KEY-----"
        )
    enc_pw = rsa_encrypt(pub, pw)
    # Spring Security 表单登录（URL 带 jsessionid，与页面一致）
    url = f"{BASE}/j_spring_security_check;jsessionid={jsid}"
    resp = s.post(
        url,
        data={"j_username": user, "j_password": enc_pw},
        timeout=20,
        allow_redirects=False,
    )
    # 登录成功常见：302 到首页；失败回登录页
    loc = resp.headers.get("Location", "")
    body = resp.text[:500] if resp.status_code == 200 else ""
    ok = resp.status_code in (301, 302, 303) and "toLoginPage" not in loc
    if not ok and resp.status_code == 200 and "toLoginPage" in body:
        ok = False
    # 有些环境登录后 200 到主页；再探一次 DCS
    jsid2 = s.cookies.get("JSESSIONID") or jsid
    probe = s.post(
        DCS_PROBE,
        data={
            "codes": "V4.DPU1001.SH0004.AALM01.PV",
            "startTime": "2026-09-01 00:00:00",
            "endTime": "2026-09-01 01:00:00",
        },
        timeout=30,
    )
    probe_ok = False
    try:
        pj = probe.json()
        probe_ok = "data" in pj and "toLoginPage" not in probe.text[:2000]
    except ValueError:
        probe_ok = False
    print(f"login http={resp.status_code} loc={loc[:60]!r} probe_ok={probe_ok}", flush=True)
    if not probe_ok:
        raise SystemExit("登录后 DCS 探测仍失败（账号/密码或会话策略问题）")
    SESS_OUT.parent.mkdir(parents=True, exist_ok=True)
    SESS_OUT.write_text(
        json.dumps({"cookie": f"JSESSIONID={jsid2}", "jSidVar": jsid2, "pub": ""}, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"OK session saved → {SESS_OUT} (len={len(jsid2)})", flush=True)
    return jsid2


if __name__ == "__main__":
    sys.exit(0 if login() else 1)
