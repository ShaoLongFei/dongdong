"""Run：一次任务运行的可变状态与线程间通信原语。

锁的约定：Run 内部所有字段都在 self.lock 下改。加锁顺序固定为
run.lock -> store 内部锁，反过来会死锁。
"""

from __future__ import annotations

import threading
import time
from collections import deque

from .model import TERMINAL_RUN, Event, RunState


class Run:
    def __init__(self, rid: int, goal: str, timeout: float | None) -> None:
        self.id = rid
        self.goal = goal
        self.state = RunState.CREATED
        self.deadline = time.time() + timeout if timeout else None
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.paused = False
        self.settling = False
        self.inflight = 0
        self.triggers: list[Event] = []
        self.round = 0
        self.stalls = 0
        self.steer: dict[int, deque[str]] = {}
        self.actions: dict[int, str] = {}
        self.threads: list[threading.Thread] = []
        self.reason = ""
        self.started_at = 0.0
        self.finished_at = 0.0

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_RUN

    def trigger_ev(self, kind: str, text: str = "", item_id: int | None = None) -> None:
        with self.lock:
            self.triggers.append(Event(kind, text, item_id))

    def drain_triggers(self) -> list[Event]:
        with self.lock:
            out, self.triggers = self.triggers, []
            return out

    def inflight_add(self, delta: int) -> None:
        with self.lock:
            self.inflight += delta

    def push_steer(self, item_id: int, msg: str) -> None:
        with self.lock:
            self.steer.setdefault(item_id, deque()).append(msg)

    def pop_steer(self, item_id: int) -> str | None:
        with self.lock:
            queue = self.steer.get(item_id)
            return queue.popleft() if queue else None
