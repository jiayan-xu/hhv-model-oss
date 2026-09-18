# -*- coding: utf-8 -*-
"""收 DCS 小时均值 JSON：POST /save?name=xxx.json  body=json。"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

OUT = Path(__file__).resolve().parents[1] / "data" / "snmis_raw" / "dcs_hourly"
OUT.mkdir(parents=True, exist_ok=True)


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        q = parse_qs(urlparse(self.path).query)
        name = (q.get("name") or ["chunk.json"])[0]
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        dest = OUT / Path(name).name
        dest.write_bytes(body)
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(f"ok {dest.name} {len(body)}".encode())

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def log_message(self, fmt, *args):
        print(fmt % args)


if __name__ == "__main__":
    print("dcs receiver", OUT, "8768")
    HTTPServer(("127.0.0.1", 8768), H).serve_forever()
