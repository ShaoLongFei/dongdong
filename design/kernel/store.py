"""WorkStore：内核访问状态的唯一入口。

生产实现用 SQL 时，claim 必须是一条语句的 CAS：

    UPDATE items SET state='running', owner=? WHERE id=? AND state='open'

受影响行数为 1 才算抢到。别把这里的锁语义带过去——多进程时进程内锁无效。
InMemoryStore 只是把这套语义用一把锁模拟出来，供骨架跑通与接口验证。
"""

from __future__ import annotations

import threading
import time
from typing import Protocol

from .model import Goal, GoalState, Item, ItemState


class WorkStore(Protocol):
    """契约见 kernel-design.md §4.1。所有状态变更都是 CAS。"""

    # -- 目标 --
    def add_goal(self, text: str, *, parent: int | None = None) -> int: ...
    def goals(self, state: GoalState | None = None) -> list[Goal]: ...
    def prove_goal(
        self, goal_id: int, *, by_item: int | None = None, evidence: str = ""
    ) -> bool: ...

    # -- 工作项 --
    def add_item(
        self,
        text: str,
        *,
        priority: int = 0,
        parent: int | None = None,
        meta: dict | None = None,
    ) -> int: ...
    def get_item(self, item_id: int) -> Item | None: ...
    def items(self, state: ItemState | None = None) -> list[Item]: ...
    def frontier(self, limit: int = 20) -> list[Item]: ...
    def claim(self, item_id: int, owner: str) -> bool: ...
    def transition(
        self,
        item_id: int,
        frm: ItemState,
        to: ItemState,
        *,
        result: str = "",
        reason: str = "",
    ) -> bool: ...


class InMemoryStore:
    """单进程内存实现。只用于骨架演示，重启即丢。"""

    def __init__(self) -> None:
        self._mu = threading.RLock()
        self._goals: dict[int, Goal] = {}
        self._items: dict[int, Item] = {}
        self._gseq = 0
        self._iseq = 0

    # -- 目标 --

    def add_goal(self, text: str, *, parent: int | None = None) -> int:
        with self._mu:
            self._gseq += 1
            self._goals[self._gseq] = Goal(self._gseq, text, parent=parent)
            return self._gseq

    def goals(self, state: GoalState | None = None) -> list[Goal]:
        with self._mu:
            return [g for g in self._goals.values() if state is None or g.state is state]

    def prove_goal(self, goal_id: int, *, by_item: int | None = None, evidence: str = "") -> bool:
        with self._mu:
            goal = self._goals.get(goal_id)
            if goal is None or goal.state is not GoalState.OPEN:
                return False
            goal.state = GoalState.MET
            goal.proof = evidence
            goal.proved_by = by_item
            return True

    # -- 工作项 --

    def add_item(
        self,
        text: str,
        *,
        priority: int = 0,
        parent: int | None = None,
        meta: dict | None = None,
    ) -> int:
        with self._mu:
            self._iseq += 1
            self._items[self._iseq] = Item(
                self._iseq, text, priority=priority, parent=parent, meta=dict(meta or {})
            )
            return self._iseq

    def get_item(self, item_id: int) -> Item | None:
        with self._mu:
            return self._items.get(item_id)

    def items(self, state: ItemState | None = None) -> list[Item]:
        with self._mu:
            return [i for i in self._items.values() if state is None or i.state is state]

    def frontier(self, limit: int = 20) -> list[Item]:
        with self._mu:
            ready = [i for i in self._items.values() if i.state is ItemState.OPEN]
        ready.sort(key=lambda i: (-i.priority, i.id))
        return ready[:limit]

    def claim(self, item_id: int, owner: str) -> bool:
        with self._mu:
            item = self._items.get(item_id)
            if item is None or item.state is not ItemState.OPEN:
                return False
            item.state = ItemState.RUNNING
            item.owner = owner
            item.attempts += 1
            item.started_at = time.time()
            return True

    def transition(
        self,
        item_id: int,
        frm: ItemState,
        to: ItemState,
        *,
        result: str = "",
        reason: str = "",
    ) -> bool:
        with self._mu:
            item = self._items.get(item_id)
            if item is None or item.state is not frm:
                return False
            item.state = to
            if result:
                item.result = result
            if reason:
                item.reason = reason
            if to in {ItemState.DONE, ItemState.BLOCKED, ItemState.EXHAUSTED, ItemState.STOPPED}:
                item.ended_at = time.time()
            return True
