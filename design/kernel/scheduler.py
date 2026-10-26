"""调度：一个 planner 线程 + N 个 worker 线程组成一个 Run。

移植自 ARTEX 的 server/engine.go#plannerLoop / #workerLoop / #claimNext。
区别只有一处：所有对状态的读写都收敛到 WorkStore 接口，不再认识「探索图」。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from .model import (
    ITEM_TERMINAL,
    TERMINAL_RUN,
    Decision,
    GoalState,
    Item,
    ItemState,
    Result,
    ResultKind,
    RunState,
)
from .planner import Planner, View
from .run import Run
from .runner import RunContext, Runner
from .settle import deadline_loop, settle_item, settle_run
from .store import WorkStore


class Kernel:
    def __init__(
        self,
        store: WorkStore,
        planner: Planner,
        runner: Runner,
        *,
        workers: int = 3,
        debounce: float = 0.8,
        heartbeat: float = 600.0,
        empty_poll: float = 0.2,
        stall_limit: int = 3,
        drain_grace: float = 90.0,
        logger: Callable[[str], None] = print,
    ) -> None:
        self.store = store
        self.planner = planner
        self.runner = runner
        self.workers = workers
        self.debounce = debounce
        self.heartbeat = heartbeat
        self.empty_poll = empty_poll
        self.stall_limit = stall_limit
        self.drain_grace = drain_grace
        self.log = logger
        self._runs: dict[int, Run] = {}
        self._seq = 0
        self._plan_lock = threading.Lock()

    # ---------------------------------------------------------------- 生命周期

    def submit(self, goal: str, *, timeout: float | None = None) -> Run:
        self._seq += 1
        run = Run(self._seq, goal, timeout)
        self.store.add_goal(goal)
        self._runs[run.id] = run
        return run

    def start(self, run: Run) -> None:
        with run.lock:
            run.state = RunState.RUNNING
            run.started_at = time.time()
        self._spawn(run, self._planner_loop, run)
        for n in range(self.workers):
            self._spawn(run, self._worker_loop, run, f"w{n + 1}")
        if run.deadline is not None:
            self._spawn(run, deadline_loop, self, run)
        run.wake.set()  # 起跑就规划一轮，否则要白等一个心跳周期

    def run_to_completion(self, goal: str, *, timeout: float | None = 30.0) -> Run:
        run = self.submit(goal, timeout=timeout)
        self.start(run)
        self.wait(run)
        return run

    def _spawn(self, run: Run, fn, *args) -> None:
        t = threading.Thread(target=fn, args=args, daemon=True, name=f"run{run.id}")
        with run.lock:
            run.threads.append(t)
        t.start()

    def wait(self, run: Run, timeout: float | None = None) -> RunState:
        end = time.monotonic() + timeout if timeout else None
        while not run.terminal:
            if end is not None and time.monotonic() > end:
                break
            time.sleep(0.02)
        return run.state

    # ---------------------------------------------------------------- 人工干预

    def pause(self, run: Run) -> bool:
        with run.lock:
            if run.state in TERMINAL_RUN or run.paused:
                return False
            run.paused = True
            run.state = RunState.PAUSED
        running = self.store.items(ItemState.RUNNING)
        with run.lock:
            for it in running:
                run.actions[it.id] = "pause"
        run.wake.set()
        return True

    def resume(self, run: Run) -> bool:
        with run.lock:
            if not run.paused:
                return False
            run.paused = False
            run.state = RunState.RUNNING
        run.wake.set()
        return True

    def steer(self, run: Run, item_id: int, msg: str) -> None:
        """给在跑的工作项插一句话，执行者在下一个步骤边界收到。"""
        run.push_steer(item_id, msg)
        run.wake.set()

    def kill(self, run: Run, item_id: int) -> None:
        """终止一项，不回写结果。在跑的转 stopped；还没跑的也直接关掉。"""
        if any(it.id == item_id for it in self.store.items(ItemState.RUNNING)):
            with run.lock:
                run.actions[item_id] = "kill"
            run.wake.set()
            return
        self.store.transition(item_id, ItemState.OPEN, ItemState.STOPPED, reason="killed")

    def cancel(self, run: Run) -> None:
        threading.Thread(
            target=settle_run, args=(self, run, RunState.CANCELED, "被取消"), daemon=True
        ).start()

    # ---------------------------------------------------------------- 规划循环

    def _planner_loop(self, run: Run) -> None:
        last = time.monotonic()
        while not run.stop.is_set():
            left = self.heartbeat - (time.monotonic() - last)
            if run.wake.wait(max(0.05, left)):
                run.wake.clear()
                source = "edge"
                # debounce：把一阵连续的变动合并成一轮，别让 N 个 worker 触发 N 轮规划
                end = time.monotonic() + self.debounce
                while (gap := end - time.monotonic()) > 0:
                    if not run.wake.wait(gap):
                        break
                    run.wake.clear()
            else:
                source = "heartbeat"
            last = time.monotonic()
            if run.stop.is_set() or run.terminal or run.settling:
                continue
            with self._plan_lock:
                self._plan_once(run, source)

    def _plan_once(self, run: Run, source: str, final: bool = False) -> None:
        if run.terminal:
            return
        if self._open_goals() == 0:
            # 无 open 目标 = 人工直投模式：不规划，等工作项跑干就收尾
            if not self._active():
                self.finish(run, RunState.DONE, "目标已全部达成")
            return
        run.round += 1
        view = View(
            goal=run.goal,
            goals=self.store.goals(),
            items=self.store.items(),
            triggers=run.drain_triggers(),
            round=run.round,
            stalled=self._stalled(),
            final=final,
        )
        try:
            decision = self.planner.plan(view)
        except Exception as exc:  # 规划者崩了不该杀掉任务，下一轮再试
            self.log(f"[planner] 第 {run.round} 轮失败：{type(exc).__name__}: {exc}")
            return
        self._apply(run, decision, source)

    def _apply(self, run: Run, d: Decision, source: str) -> None:
        for item_id in d.kill:
            self.kill(run, item_id)
        for item_id, msg in d.steer:
            self.steer(run, item_id, msg)
        for goal in d.add_goals:
            self.store.add_goal(goal.text, parent=goal.parent)
        for new in d.add_items:
            self.store.add_item(new.text, priority=new.priority, parent=new.parent, meta=new.meta)
        for goal_id, evidence in d.prove:
            self.store.prove_goal(goal_id, evidence=evidence)
        self.log(
            f"[planner] 第 {run.round} 轮（{source}）：+{len(d.add_items)} 项 / "
            f"验收 {len(d.prove)} 个目标"
        )
        if d.finish:
            self.finish(run, RunState.DONE, d.reason or "规划者判定完成")
            return
        # 只验收、没补活：立刻再规划一轮。否则这一步要白等一个心跳周期。
        if d.prove and d.empty_items():
            run.wake.set()
        if self._open_goals() > 0 and not self._active() and not d.add_items:
            run.stalls += 1
            if run.stalls >= self.stall_limit:
                self.finish(run, RunState.FAILED, f"连续 {run.stalls} 轮无产出，判定停摆")
        else:
            run.stalls = 0

    def final_round(self, run: Run) -> None:
        with self._plan_lock:
            self._plan_once(run, "final", final=True)

    # ---------------------------------------------------------------- 执行循环

    def _worker_loop(self, run: Run, name: str) -> None:
        while not run.stop.is_set():
            if run.terminal:
                return
            if run.paused or run.settling:
                run.stop.wait(self.empty_poll)
                continue
            item = self._claim(run, name)
            if item is None:
                run.stop.wait(self.empty_poll)
                continue
            self._execute(run, item, name)

    def _claim(self, run: Run, name: str) -> Item | None:
        for candidate in self.store.frontier(20):
            if self.store.claim(candidate.id, name):
                run.inflight_add(1)
                return self.store.get_item(candidate.id)
        return None

    def _execute(self, run: Run, item: Item, name: str) -> None:
        ctx = RunContext(
            run,
            item,
            owner=name,
            emit=lambda text: self.on_output(item, name, text),
        )
        try:
            result = self.runner.run(item, ctx)
        except Exception as exc:
            result = Result(ResultKind.BLOCKED, reason=f"{type(exc).__name__}: {exc}")
        finally:
            run.inflight_add(-1)
        settle_item(self, run, item, result)

    def on_output(self, item: Item, owner: str, text: str) -> None:
        self.log(f"    [{owner} #{item.id}] {text}")

    # ---------------------------------------------------------------- 状态收尾

    def finish(self, run: Run, state: RunState, reason: str) -> None:
        with run.lock:
            if run.state in TERMINAL_RUN:
                return
            run.state = state
            run.reason = reason
            run.finished_at = time.time()
        running = self.store.items(ItemState.RUNNING)
        with run.lock:
            for it in running:
                run.actions[it.id] = "kill"
        run.stop.set()
        run.wake.set()
        self.log(f"[run {run.id}] → {state}：{reason}")

    def _open_goals(self) -> int:
        return len(self.store.goals(GoalState.OPEN))

    def _active(self) -> bool:
        return any(it.state not in ITEM_TERMINAL for it in self.store.items())

    def _stalled(self) -> bool:
        return self._open_goals() > 0 and not self._active()
