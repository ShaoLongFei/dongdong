"""收尾：单项结算、任务级超时、drain 时序。

移植自 ARTEX 的 server/engine.go#runIntent（终态分流）与 server/engine_timeout.go
（settling / drain / 最后规划轮）。
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from .model import TERMINAL_RUN, ItemState, Result, ResultKind, RunState

if TYPE_CHECKING:  # 只用于类型标注，避免循环导入
    from .run import Run
    from .scheduler import Kernel


def settle_item(kernel: Kernel, run: Run, item, result: Result) -> None:
    """把一次执行的结局落库。控制指令优先于执行者自己报的结果。"""
    with run.lock:
        action = run.actions.pop(item.id, "")
        terminal = run.state in TERMINAL_RUN
    store = kernel.store

    if action == "pause":
        store.transition(item.id, ItemState.RUNNING, ItemState.OPEN, reason="paused")
        return
    if action == "kill" or terminal or run.stop.is_set():
        store.transition(
            item.id, ItemState.RUNNING, ItemState.STOPPED, reason=action or "run-ended"
        )
        return

    target = {
        ResultKind.DONE: ItemState.DONE,
        ResultKind.BLOCKED: ItemState.BLOCKED,
        ResultKind.EXHAUSTED: ItemState.EXHAUSTED,
    }[result.kind]
    if not store.transition(
        item.id, ItemState.RUNNING, target, result=result.summary, reason=result.reason
    ):
        return  # CAS 失败说明别人已经改过，不覆盖
    run.trigger_ev(target.value, result.summary, item.id)
    run.wake.set()


def deadline_loop(kernel: Kernel, run: Run) -> None:
    """每个带超时的任务一个线程，到点驱动有序收尾。"""
    assert run.deadline is not None
    while not run.stop.is_set():
        left = run.deadline - time.time()
        if left <= 0:
            settle_run(kernel, run, RunState.TIMEOUT, "任务超时")
            return
        run.stop.wait(min(left, 1.0))


def settle_run(kernel: Kernel, run: Run, state: RunState, reason: str) -> None:
    """有序收尾：停派 → 等在跑的清场 → 最后给规划者一轮 → 定终态。"""
    with run.lock:
        if run.state in TERMINAL_RUN or run.settling:
            return
        run.settling = True
    run.wake.set()
    kernel.log(f"[settle] {run.id} 开始收尾（{reason}）")

    end = time.monotonic() + kernel.drain_grace
    while time.monotonic() < end:
        with run.lock:
            if run.inflight == 0:
                break
        time.sleep(0.05)

    kernel.final_round(run)
    kernel.finish(run, state, reason)
