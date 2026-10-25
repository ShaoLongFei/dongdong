"""进程内共享状态，给前台面板读。不持久化，重启即清空。"""

from __future__ import annotations

import threading
import time
from collections import deque

_lock = threading.Lock()
_data: dict = {
    "connection": "starting",  # starting | connecting | connected | disconnected
    "session_id": "",
    "connected_at": 0.0,
    "reconnects": 0,
    "last_stt": "",
    "last_tts": "",
    "calls": deque(maxlen=30),
}


def set_(**kw) -> None:
    with _lock:
        _data.update(kw)


def record_call(name: str, arguments: dict, ms: int, result: dict) -> None:
    preview = json_short(arguments)
    text = (result.get("content") or [{}])[0].get("text", "")
    with _lock:
        _data["calls"].appendleft(
            {
                "at": time.strftime("%H:%M:%S"),
                "name": name,
                "args": preview,
                "ms": ms,
                "ok": not result.get("isError", False),
                "text": text[:300],
            }
        )


def snapshot(cfg: dict) -> dict:
    from . import agent, tools

    with _lock:
        conn = _data["connection"]
        session = _data["session_id"]
        connected_at = _data["connected_at"]
        payload = {
            "device_id": cfg.get("device_id", ""),
            "connection": conn,
            "session_id": session,
            "uptime": int(time.time() - connected_at) if connected_at else 0,
            "reconnects": _data["reconnects"],
            "last_stt": _data["last_stt"],
            "last_tts": _data["last_tts"],
            "calls": list(_data["calls"]),
        }
    if cfg.get("code_backend") == "builtin":
        payload["job"] = tools.service_snapshot()
        payload["default_agent"] = "builtin"
    else:
        payload["job"] = agent.snapshot()
        payload["default_agent"] = cfg.get("agent", "")
    payload["agents"] = list((cfg.get("agents") or {}).keys())
    return payload


def json_short(value, limit: int = 120) -> str:
    import json

    try:
        text = json.dumps(value, ensure_ascii=False)
    except Exception:
        text = str(value)
    return text if len(text) <= limit else text[:limit] + "…"
