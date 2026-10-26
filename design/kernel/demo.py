"""跑一遍骨架：3 个 worker、两波拆解、一次预算耗尽重试、一次外部纠正。"""

from __future__ import annotations

import threading
import time

from .model import ItemState
from .planner import DemoPlanner
from .runner import DemoRunner
from .scheduler import Kernel
from .store import InMemoryStore


def main() -> None:
    store = InMemoryStore()
    kernel = Kernel(
        store,
        DemoPlanner(),
        DemoRunner(),
        workers=3,
        debounce=0.15,
        heartbeat=2.0,
        empty_poll=0.05,
        drain_grace=2.0,
        logger=lambda line: print(line, flush=True),
    )
    run = kernel.submit("给项目补一份中文 README", timeout=30)
    kernel.start(run)

    def steer_later() -> None:
        end = time.time() + 10
        while time.time() < end:
            for item in store.items(ItemState.RUNNING):
                if item.text == "翻译正文（重试）":
                    kernel.steer(run, item.id, "保留原有的命令示例")
                    print(f"[demo] 已向 #{item.id} 发出纠正", flush=True)
                    return
            time.sleep(0.02)
        print("[demo] 没抓到在跑的重试项，跳过纠正演示")

    threading.Thread(target=steer_later, daemon=True).start()
    kernel.wait(run, timeout=30)

    elapsed = run.finished_at - run.started_at
    print(f"\n== 结果：{run.state}（{run.reason}），耗时 {elapsed:.2f}s ==")
    for item in sorted(store.items(), key=lambda i: i.id):
        print(f"  #{item.id} {item.text:<16} {item.state:<10} attempts={item.attempts}")
    for goal in store.goals():
        print(f"  目标「{goal.text}」{goal.state}  证据：{goal.proof}")


if __name__ == "__main__":
    main()
