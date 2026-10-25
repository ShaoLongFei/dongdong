"""前台面板：本地网页，看状态、派任务。

跟 `dongdong run` 起在同一个进程里，所以 `state` 和 `agent` 的内存直接可读，
不需要跨进程通信。面板挂了不影响语音，只记一条警告。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import agent, state

log = logging.getLogger("dongdong.panel")

PAGE = Path(__file__).with_name("panel.html")


def start(cfg: dict) -> None:
    host = cfg["panel"]["host"]
    port = int(cfg["panel"]["port"])
    try:
        server = ThreadingHTTPServer((host, port), make_handler(cfg))
    except OSError as exc:
        log.warning("面板起不来（%s），语音功能不受影响", exc)
        return
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info("面板 http://%s:%d", host, port)


def make_handler(cfg: dict):
    token = (cfg.get("panel") or {}).get("token") or ""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/":
                return self._page()
            if path == "/state":
                if not self._auth():
                    return self._json(401, {"error": "bad token"})
                return self._json(200, state.snapshot(cfg))
            self._json(404, {"error": "not found"})

        def do_POST(self) -> None:
            if not self._auth():
                return self._json(401, {"error": "bad token"})
            if self.path != "/task":
                return self._json(404, {"error": "not found"})
            req = self._body()
            if req is None:
                return
            instruction = (req.get("instruction") or "").strip()
            if not instruction:
                return self._json(400, {"error": "instruction 不能为空"})
            if cfg.get("code_backend") == "builtin":
                return self._json(
                    409, {"error": "builtin 模式请用语音派任务；面板任务还没接 Runtime"}
                )
            name = (req.get("agent") or cfg["agent"]).strip()
            preset = cfg["agents"].get(name)
            if not preset:
                return self._json(400, {"error": f"未知 agent: {name}"})
            missing = [v for v in agent.required_env(preset) if not os.environ.get(v)]
            if missing:
                return self._json(400, {"error": f"缺少环境变量: {', '.join(missing)}"})
            try:
                workdir = agent.resolve_dir(cfg, req.get("cwd"))
            except ValueError as exc:
                return self._json(400, {"error": str(exc)})
            busy = agent.current()
            if busy:
                return self._json(409, {"error": f"任务 {busy.id}（{busy.agent}）还在跑，等它结束"})
            # 不排队：agent.run 里已经有「一次只跑一个」的判断，这里先挡掉并立刻返回
            threading.Thread(
                target=agent.run,
                args=(cfg, instruction, name, str(workdir)),
                kwargs={"wait_seconds": 1},
                daemon=True,
            ).start()
            self._json(202, {"ok": True, "agent": name, "cwd": str(workdir)})

        def _body(self) -> dict | None:
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._json(400, {"error": "bad json"})
                return None
            if not isinstance(body, dict):
                self._json(400, {"error": "body 必须是对象"})
                return None
            return body

        def _page(self) -> None:
            try:
                body = PAGE.read_bytes()
            except FileNotFoundError:
                return self._json(500, {"error": "panel.html 丢了"})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _auth(self) -> bool:
            return not token or self.headers.get("X-Dongdong-Token") == token

        def log_message(self, fmt: str, *args) -> None:
            log.debug("%s %s", self.command, self.path)

    return Handler
