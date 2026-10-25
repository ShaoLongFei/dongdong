"""跨 Web / 语音的任务闸门：同一时间只允许一个 agent 改工作区。"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_owner = ""


def acquire(owner: str) -> bool:
    global _owner
    with _lock:
        if _owner:
            return False
        _owner = owner
        return True


def release(owner: str) -> None:
    global _owner
    with _lock:
        if _owner == owner:
            _owner = ""


def current() -> str:
    with _lock:
        return _owner
