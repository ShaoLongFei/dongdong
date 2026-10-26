"""Runner：真正干活的东西。内核不实现 agent loop，只调用它。

两种实现：
- DemoRunner       演示用，睡一会、打几行日志、回应纠正
- SubprocessRunner 生产用，起 codex / claude / 任意 CLI 子进程（对应 dongdong 的 agent.py）

Runner 必须协作式配合停止：ctx.should_stop() 为真时尽快返回，别赖着不走。
"""

from __future__ import annotations

import subprocess
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

from .model import Item, Result, ResultKind


class RunContext:
    """执行者能看到的一切：输出、纠正、停止信号、剩余时间。"""

    def __init__(
        self,
        run: Any,
        item: Item,
        *,
        owner: str = "",
        emit: Callable[[str], None] | None = None,
    ) -> None:
        self.run = run
        self.item = item
        self.owner = owner
        self._emit = emit or (lambda _text: None)

    def emit(self, text: str) -> None:
        self._emit(text)

    def steer(self) -> str | None:
        """取一条纠正（没有就返回 None）。执行者在每个步骤边界调一次。"""
        return self.run.pop_steer(self.item.id)

    def should_stop(self) -> bool:
        if self.run.stop.is_set() or self.run.paused:
            return True
        if self.run.actions.get(self.item.id) is not None:
            return True
        return self.run.deadline is not None and time.time() >= self.run.deadline

    def remaining(self) -> float | None:
        if self.run.deadline is None:
            return None
        return self.run.deadline - time.time()


class Runner(Protocol):
    def run(self, item: Item, ctx: RunContext) -> Result: ...


class DemoRunner:
    """三步走完一项；「翻译正文」第一次故意耗尽预算，用来演示 EXHAUSTED。"""

    def __init__(self, step: float = 0.2) -> None:
        self.step = step

    def run(self, item: Item, ctx: RunContext) -> Result:
        if item.text == "翻译正文" and item.attempts == 1:
            time.sleep(self.step)
            ctx.emit("预算耗尽，正文只翻了一半")
            return Result(ResultKind.EXHAUSTED, reason="步数预算耗尽")

        for n in range(1, 4):
            if ctx.should_stop():
                return Result(ResultKind.BLOCKED, reason="被停止")
            time.sleep(self.step)
            ctx.emit(f"进度 {n}/3")
            msg = ctx.steer()
            if msg:
                ctx.emit(f"收到纠正：{msg}，按新要求重排")
        return Result(ResultKind.DONE, summary=f"{item.text} 完成")


class SubprocessRunner:
    """把一项工作项变成一次 CLI agent 调用。

    cmd 模板里 {prompt} 会替换成工作项文本，例如：
        ["codex", "exec", "--cd", "/path/repo", "{prompt}"]
    """

    def __init__(self, cmd: list[str], *, cwd: str | None = None, timeout_s: float = 600.0) -> None:
        self.cmd = cmd
        self.cwd = cwd
        self.timeout_s = timeout_s

    def run(self, item: Item, ctx: RunContext) -> Result:
        cmd = [part.format(prompt=item.text) for part in self.cmd]
        proc = subprocess.Popen(
            cmd,
            cwd=self.cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        killed = threading.Event()

        def watchdog() -> None:
            end = time.monotonic() + self.timeout_s
            while proc.poll() is None:
                if ctx.should_stop() or time.monotonic() > end:
                    killed.set()
                    proc.kill()
                    return
                time.sleep(0.1)

        threading.Thread(target=watchdog, daemon=True).start()
        tail = ""
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                ctx.emit(line)
                tail = line
        code = proc.wait()
        if killed.is_set():
            return Result(ResultKind.EXHAUSTED, reason="被停止或超时")
        if code != 0:
            return Result(ResultKind.BLOCKED, reason=f"退出码 {code}：{tail}")
        return Result(ResultKind.DONE, summary=tail[-200:] or "完成")
