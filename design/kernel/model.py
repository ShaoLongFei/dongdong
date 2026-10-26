"""内核的领域类型：目标、工作项、事件、决策、结果。

这里只有数据与状态常量，没有行为。状态机的推进入口全在 store / settle。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class RunState(StrEnum):
    """一次任务运行的终态只有三种，其余都是进行中。"""

    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    DONE = "done"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELED = "canceled"


TERMINAL_RUN = frozenset({RunState.DONE, RunState.FAILED, RunState.TIMEOUT, RunState.CANCELED})


class ItemState(StrEnum):
    OPEN = "open"
    RUNNING = "running"
    DONE = "done"
    BLOCKED = "blocked"
    EXHAUSTED = "exhausted"
    STOPPED = "stopped"


ITEM_TERMINAL = frozenset(
    {ItemState.DONE, ItemState.BLOCKED, ItemState.EXHAUSTED, ItemState.STOPPED}
)


class GoalState(StrEnum):
    OPEN = "open"
    MET = "met"
    ABANDONED = "abandoned"


class ResultKind(StrEnum):
    """执行者只回报三种；STOPPED 由内核根据控制指令推断，执行者无权返回。"""

    DONE = "done"
    BLOCKED = "blocked"  # 出错。可以重试，规划者应看到故障
    EXHAUSTED = "exhausted"  # 预算/步数耗尽。方向试过但没做完，不代表失败


@dataclass
class Goal:
    id: int
    text: str
    state: GoalState = GoalState.OPEN
    parent: int | None = None
    proof: str = ""
    proved_by: int | None = None


@dataclass
class Item:
    id: int
    text: str
    state: ItemState = ItemState.OPEN
    priority: int = 0
    owner: str = ""
    attempts: int = 0
    result: str = ""
    reason: str = ""
    parent: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    ended_at: float = 0.0


@dataclass
class Event:
    """唤醒规划者的一条「发生了什么」。只有 kind 是契约，其余是给人看的。"""

    kind: str
    text: str = ""
    item_id: int | None = None
    at: float = field(default_factory=time.time)


@dataclass
class NewGoal:
    text: str
    parent: int | None = None


@dataclass
class NewItem:
    text: str
    priority: int = 0
    parent: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Decision:
    """规划者一轮的全部产出。内核负责落地，规划者不直接写库。

    顺序由内核固定：先 kill/steer（影响在跑的），再补目标与工作项，最后验收与收尾。
    """

    add_goals: list[NewGoal] = field(default_factory=list)
    add_items: list[NewItem] = field(default_factory=list)
    prove: list[tuple[int, str]] = field(default_factory=list)
    steer: list[tuple[int, str]] = field(default_factory=list)
    kill: list[int] = field(default_factory=list)
    finish: bool = False
    reason: str = ""

    def empty_items(self) -> bool:
        return not self.add_items and not self.add_goals

    def empty(self) -> bool:
        return not (
            self.add_goals or self.add_items or self.prove or self.steer or self.kill or self.finish
        )


@dataclass
class Result:
    kind: ResultKind
    summary: str = ""
    reason: str = ""
