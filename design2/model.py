"""状态与数据对象。没有循环，没有调度，只有"什么是合法的状态"。

所有状态变更都走 Store 的单个写方法：前置状态不匹配就返回 False。
这就是 CAS 语义，进程内用一把锁模拟。生产换 SQL 时只改 Store：

    UPDATE work SET state='running', owner=?, attempts=attempts+1
     WHERE id=? AND state='open'          -- 影响行数为 1 才算抢到

别把这里的锁带过去：多进程时它什么也不保证。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field, replace
from enum import StrEnum


class WorkState(StrEnum):
    OPEN = "open"
    RUNNING = "running"
    DONE = "done"
    BLOCKED = "blocked"  # 出错，要人看
    EXHAUSTED = "exhausted"  # 预算/时间用完，方向未必错
    STOPPED = "stopped"  # 人叫停，或整轮收尾


TERMINAL = frozenset({WorkState.DONE, WorkState.BLOCKED, WorkState.EXHAUSTED, WorkState.STOPPED})


class GoalState(StrEnum):
    OPEN = "open"
    MET = "met"
    ABANDONED = "abandoned"


class ResultKind(StrEnum):
    """执行者只允许报这三种终局；stopped 由内核推断，执行者无权返回。"""

    DONE = "done"
    BLOCKED = "blocked"
    EXHAUSTED = "exhausted"


RESULT_STATE = {
    ResultKind.DONE: WorkState.DONE,
    ResultKind.BLOCKED: WorkState.BLOCKED,
    ResultKind.EXHAUSTED: WorkState.EXHAUSTED,
}


@dataclass(frozen=True)
class Goal:
    """要证明的命题，不是要做的事。没有 proof 就不算达成。"""

    id: int
    text: str
    state: GoalState = GoalState.OPEN
    parent: int | None = None
    depth: int = 0
    proof: str = ""


@dataclass(frozen=True)
class Work:
    """可派发的最小单元。终态不回退；要重做就新建。"""

    id: int
    text: str
    state: WorkState = WorkState.OPEN
    goal_id: int | None = None
    priority: int = 0
    owner: str = ""
    attempts: int = 0
    result: str = ""
    reason: str = ""
    retry_of: int | None = None
    ended_at: float = 0.0
    delivered: bool = False


@dataclass
class NewGoal:
    text: str
    parent: int | None = None


@dataclass
class NewWork:
    text: str
    goal_id: int | None = None
    priority: int = 0
    retry_of: int | None = None


@dataclass
class Decision:
    """一轮规划的全部产出。内核按 kill → 目标 → 工作项 → 验收 → 收尾 落地。"""

    goals: list[NewGoal] = field(default_factory=list)
    works: list[NewWork] = field(default_factory=list)
    prove: list[tuple[int, str]] = field(default_factory=list)
    kill: list[int] = field(default_factory=list)
    finish: bool = False
    reason: str = ""


@dataclass
class Result:
    kind: ResultKind
    summary: str = ""
    reason: str = ""


class Store:
    """所有状态变更的唯一入口。改不动的东西在这里改不动。"""

    def __init__(self) -> None:
        self._mu = threading.RLock()
        self._goals: dict[int, Goal] = {}
        self._works: dict[int, Work] = {}
        self._seq = 0

    def _next(self) -> int:
        self._seq += 1
        return self._seq

    # -- 目标 -------------------------------------------------------------

    def add_goal(self, text: str, parent: int | None = None) -> int:
        with self._mu:
            parent_goal = self._goals.get(parent) if parent is not None else None
            gid = self._next()
            depth = 0 if parent_goal is None else parent_goal.depth + 1
            self._goals[gid] = Goal(gid, text, parent=parent, depth=depth)
            return gid

    def prove(self, goal_id: int, proof: str) -> bool:
        with self._mu:
            goal = self._goals.get(goal_id)
            if goal is None or goal.state is not GoalState.OPEN:
                return False
            self._goals[goal_id] = replace(goal, state=GoalState.MET, proof=proof)
            return True

    def goals(self) -> list[Goal]:
        with self._mu:
            return [self._goals[k] for k in sorted(self._goals)]

    # -- 工作项 -----------------------------------------------------------

    def add_work(self, new: NewWork) -> int:
        with self._mu:
            wid = self._next()
            self._works[wid] = Work(
                wid, new.text, goal_id=new.goal_id, priority=new.priority, retry_of=new.retry_of
            )
            return wid

    def claim(self, work_id: int, owner: str) -> bool:
        with self._mu:
            work = self._works.get(work_id)
            if work is None or work.state is not WorkState.OPEN:
                return False
            self._works[work_id] = replace(
                work, state=WorkState.RUNNING, owner=owner, attempts=work.attempts + 1
            )
            return True

    def settle(self, work_id: int, to: WorkState, result: str, reason: str) -> bool:
        with self._mu:
            work = self._works.get(work_id)
            if work is None or work.state is not WorkState.RUNNING:
                return False
            self._works[work_id] = replace(
                work, state=to, result=result, reason=reason, ended_at=time.time()
            )
            return True

    def stop_open(self, work_id: int, reason: str) -> bool:
        """取消排队中的项。在跑的那条走 settle 的覆盖路径。"""
        with self._mu:
            work = self._works.get(work_id)
            if work is None or work.state is not WorkState.OPEN:
                return False
            self._works[work_id] = replace(
                work, state=WorkState.STOPPED, reason=reason, ended_at=time.time()
            )
            return True

    def deliver(self, work_ids: list[int]) -> None:
        with self._mu:
            for wid in work_ids:
                work = self._works.get(wid)
                if work is not None:
                    self._works[wid] = replace(work, delivered=True)

    # -- 查询 -------------------------------------------------------------

    def works(
        self,
        state: WorkState | None = None,
        *,
        limit: int = 100,
        newest: bool = False,
    ) -> list[Work]:
        with self._mu:
            items = [w for w in self._works.values() if state is None or w.state is state]
        if newest:
            items.sort(key=lambda w: (-w.ended_at, w.id))
        else:
            items.sort(key=lambda w: (-w.priority, w.id))
        return items[:limit]

    def undelivered(self, limit: int) -> list[Work]:
        with self._mu:
            items = [w for w in self._works.values() if w.state in TERMINAL and not w.delivered]
        items.sort(key=lambda w: (w.ended_at, w.id))
        return items[:limit]

    def counts(self) -> dict[str, int]:
        with self._mu:
            out: dict[str, int] = {}
            for work in self._works.values():
                out[work.state.value] = out.get(work.state.value, 0) + 1
            return out

    def count(self, goal_id: int | None = None) -> int:
        with self._mu:
            if goal_id is None:
                return len(self._works)
            return sum(1 for w in self._works.values() if w.goal_id == goal_id)

    def exists(self, goal_id: int | None, text: str) -> bool:
        with self._mu:
            return any(w.goal_id == goal_id and w.text == text for w in self._works.values())
