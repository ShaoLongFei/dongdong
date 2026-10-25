"""会话任务分别互斥；无 scope 的配置/旧设备入口仍独占整个进程。"""

import threading

_lock = threading.Lock()
_owners = {}


def acquire(owner: str, scope: str = "") -> bool:
    with _lock:
        if (not scope and _owners) or "" in _owners or scope in _owners:
            return False
        _owners[scope] = owner
        return True


def release(owner: str) -> None:
    with _lock:
        for scope in [key for key, value in _owners.items() if value == owner]:
            del _owners[scope]


def current(scope: str | None = None) -> str:
    with _lock:
        if scope is None:
            return next(iter(_owners.values()), "")
        return _owners.get("", "") or _owners.get(scope, "")
