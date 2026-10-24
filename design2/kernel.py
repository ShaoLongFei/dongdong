"""三个循环：调度、规划落地、监察快照。

  调度  open --claim--> running --终态--> 事件
  规划  snapshot --> Decision --> 落地（规划者不写库）
  监察  snapshot() 生成有界快照，喂规划者，也喂汇报

所有副作用收敛在这一个文件里。改之前先看 README §5 的六条不变量。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from queue import Empty, Queue
from typing import Protocol

from model import (
    RESULT_STATE,
    Decision,
    Goal,
    NewGoal,
    NewWork,
    Result,
    ResultKind,
    Store,
    Work,
    WorkState,
)


@dataclass
class Snapshot:
    """一轮能看到的全部信息。全部有界；超出的只剩计数，看不见明细。"""

    goals: list[Goal]
    open: list[Work]
    running: list[Work]
    finished: list[Work]
    undelivered: list[Work]
    counts: dict[str, int]
    refusals: list[str]

    @property
    def total(self) -> int:
        return sum(self.counts.values())


class Ctx:
    """执行者拿到的一切：进度出口、停止信号。没有 store，也没有别的项。"""

    def __init__(self, kernel: Kernel, work: Work) -> None:
        self._kernel = kernel
        self.work = work

    def emit(self, text: str) -> None:
        self._kernel.emit(self.work.id, text)

    def should_stop(self) -> bool:
        return self._kernel.should_stop(self.work.id)


class Runner(Protocol):
    def run(self, work: Work, ctx: Ctx) -> Result: ...


class Planner(Protocol):
    def plan(self, snap: Snapshot, final: bool) -> Decision: ...


class Kernel:
    """调度 + 规划落地。不实现 agent loop，只调用 Runner。"""

    def __init__(
        self,
        planner: Planner,
        runner: Runner,
        *,
        store: Store | None = None,
        max_inflight: int = 4,
        max_depth: int = 3,
        max_children: int = 8,
        max_total: int = 200,
        max_rounds: int = 1000,
        emit: Callable[[int, str], None] | None = None,
    ) -> None:
        self.store = store or Store()
        self.planner = planner
        self.runner = runner
        self.max_inflight = max_inflight
        self.max_depth = max_depth
        self.max_children = max_children
        self.max_total = max_total
        self.max_rounds = max_rounds
        self.refusals: list[str] = []
        self.reason = ""
        self.finished = False
        self.rounds = 0
        self._emit = emit or (lambda _id, _text: None)
        self._pool = ThreadPoolExecutor(max_workers=max_inflight)
        self._done: Queue[tuple[int, Result]] = Queue()
        self._inflight: dict[int, object] = {}
        self._stopped: set[int] = set()
        self._stopping = False
        self._final_done = False

    # -- 人的入口 ---------------------------------------------------------

    def start(self, objective: str) -> int:
        return self.store.add_goal(objective)

    def kill(self, work_id: int) -> None:
        """叫停一项。排队中的立刻终态；在跑的等它交回控制权后覆盖。"""
        self._stopped.add(work_id)
        self.store.stop_open(work_id, "killed")

    def consume(self, work_ids: list[int]) -> None:
        """汇报完成。这之后快照里不再出现这些结果，重启也不会重报。"""
        self.store.deliver(work_ids)

    # -- 执行者可见 -------------------------------------------------------

    def emit(self, work_id: int, text: str) -> None:
        # 进度只做实时展示，不落库：落库的是终态和证据，不是日志
        self._emit(work_id, text)

    def should_stop(self, work_id: int) -> bool:
        return self._stopping or work_id in self._stopped

    # -- 监察 -------------------------------------------------------------

    def snapshot(self, *, running_cap: int = 16, finished_cap: int = 8) -> Snapshot:
        return Snapshot(
            goals=self.store.goals(),
            open=self.store.works(WorkState.OPEN, limit=20),
            running=self.store.works(WorkState.RUNNING, limit=running_cap),
            finished=self.store.works(limit=finished_cap, newest=True),
            undelivered=self.store.undelivered(8),
            counts=self.store.counts(),
            refusals=self.refusals[-8:],
        )

    # -- 循环 -------------------------------------------------------------

    def tick(self) -> bool:
        """一次心跳：收结果 → 规划 → 派发。返回 False 表示整轮已收尾。"""
        self.rounds += 1
        progressed = self._collect() > 0
        if self._stopping:
            if self._inflight:
                return True
            if not self._final_done:
                self._final_done = True
                self._plan(final=True)
            return False
        if progressed or self._idle():
            if not self._plan(final=False) and self._idle():
                self._stopping = True
                self.reason = "停滞：没有可派发的项，规划者也没有新动作"
                if self.refusals:
                    self.reason += f"（最近一次拒绝：{self.refusals[-1]}）"
                return True
        self._dispatch()
        return True

    def run(self) -> None:
        try:
            while self.rounds < self.max_rounds and self.tick():
                time.sleep(0.005)
        finally:
            self._pool.shutdown(wait=True)

    # -- 规划落地 ---------------------------------------------------------

    def _idle(self) -> bool:
        return not self._inflight and not self.store.works(WorkState.OPEN, limit=1)

    def _plan(self, final: bool) -> bool:
        decision = self.planner.plan(self.snapshot(), final=final)
        changed = False
        for work_id in decision.kill:
            self.kill(work_id)
            changed = True
        for goal in decision.goals:
            changed = self._add_goal(goal) or changed
        for work in decision.works:
            changed = self._add_work(work) or changed
        for goal_id, proof in decision.prove:
            changed = self.store.prove(goal_id, proof) or changed
        if decision.finish:
            self._stopping = True
            self.finished = True
            self.reason = decision.reason or "规划者判定目标达成"
            changed = True
        return changed

    def _add_goal(self, goal: NewGoal) -> bool:
        if goal.parent is not None:
            parent = next((g for g in self.store.goals() if g.id == goal.parent), None)
            if parent is None or parent.depth + 1 >= self.max_depth:
                self._refuse(f"目标深度超限：{goal.text}")
                return False
        self.store.add_goal(goal.text, goal.parent)
        return True

    def _add_work(self, work: NewWork) -> bool:
        if self.store.count() >= self.max_total:
            self._refuse(f"总量超限：{work.text}")
            return False
        if work.goal_id is not None and self.store.count(work.goal_id) >= self.max_children:
            self._refuse(f"同目标子项超限：{work.text}")
            return False
        # 规划者只看有界快照，可能看不见早先的同类项；去重放在唯一写入口
        if self.store.exists(work.goal_id, work.text):
            self._refuse(f"已存在：{work.text}")
            return False
        self.store.add_work(work)
        return True

    def _refuse(self, why: str) -> None:
        # 拒绝必须可见，否则调用者以为已经拆了
        self.refusals.append(why)
        self._emit(0, f"[refused] {why}")

    # -- 调度 -------------------------------------------------------------

    def _dispatch(self) -> None:
        if self._stopping:
            return
        room = self.max_inflight - len(self._inflight)
        if room <= 0:
            return
        for work in self.store.works(WorkState.OPEN, limit=room):
            if self.store.claim(work.id, f"w{work.id}"):
                self._inflight[work.id] = self._pool.submit(self._execute, work)

    def _execute(self, work: Work) -> None:
        try:
            result = self.runner.run(work, Ctx(self, work))
        except Exception as err:  # 执行者崩了也要留下终态
            result = Result(ResultKind.BLOCKED, reason=f"{type(err).__name__}: {err}")
        self._done.put((work.id, result))

    def _collect(self) -> int:
        settled = 0
        while True:
            try:
                work_id, result = self._done.get_nowait()
            except Empty:
                return settled
            self._inflight.pop(work_id, None)
            settled += 1
            if work_id in self._stopped:
                # 控制优先于执行者自述：它报什么都被记成 stopped
                self.store.settle(work_id, WorkState.STOPPED, result.summary, "被叫停")
            else:
                self.store.settle(work_id, RESULT_STATE[result.kind], result.summary, result.reason)
