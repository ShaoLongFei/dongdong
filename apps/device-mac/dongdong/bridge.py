"""本地 HTTP 转发：给 ESP32 固件用的同一批工具。

GET  /tools  工具定义列表
POST /call   {"name": "...", "arguments": {...}}
"""

from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("dongdong.bridge")


def make_handler(cfg: dict):
    token = (cfg.get("bridge") or {}).get("token") or ""

    class Handler(BaseHTTPRequestHandler):
        def _json(self, code: int, obj) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            from . import tools

            if self.path != "/tools":
                return self._json(404, {"error": "not found"})
            self._json(200, [t.describe() for t in tools.TOOLS.values()])

        def do_POST(self) -> None:
            from . import tools

            if not self._auth():
                return self._json(401, {"error": "bad token"})
            if self.path != "/call":
                return self._json(404, {"error": "not found"})
            length = int(self.headers.get("Content-Length") or 0)
            try:
                req = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return self._json(400, {"error": "bad json"})
            name = req.get("name") or ""
            if name not in tools.TOOLS:
                return self._json(404, {"error": f"未知工具: {name}"})
            self._json(200, tools.call(name, req.get("arguments") or {}))

        def _auth(self) -> bool:
            return not token or self.headers.get("X-Dongdong-Token") == token

        def log_message(self, fmt: str, *args) -> None:
            log.info("%s %s", self.command, self.path)

    return Handler


def serve(cfg: dict) -> None:
    host = cfg["bridge"]["host"]
    port = int(cfg["bridge"]["port"])
    log.info("工具转发已启动 http://%s:%d（ESP32 用 /call）", host, port)
    ThreadingHTTPServer((host, port), make_handler(cfg)).serve_forever()
