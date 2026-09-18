"""本机接收 FineReport 会话导出的 xlsx（浏览器 POST，避免 CDP 截断）。"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

OUT = Path(__file__).resolve().parents[1] / "data" / "snmis_raw"
OUT.mkdir(parents=True, exist_ok=True)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        print(fmt % args)

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_POST(self) -> None:
        n = int(self.headers.get("Content-Length", "0"))
        data = self.rfile.read(n)
        q = parse_qs(urlparse(self.path).query)
        name = (q.get("name", [""])[0] or "").replace("\\", "/").split("/")[-1]
        if not name.endswith(".xlsx") or ".." in name:
            self.send_response(400)
            self._cors()
            self.end_headers()
            self.wfile.write(b"bad name")
            return
        dest = OUT / name
        dest.write_bytes(data)
        print(f"saved {dest} {len(data)} bytes")
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(f"ok {dest.name} {len(data)}".encode("utf-8"))


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", 8765), Handler).serve_forever()
