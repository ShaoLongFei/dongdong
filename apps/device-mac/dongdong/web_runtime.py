"""Web 会话任务的启动、取消和有序退出。"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from . import config, work_gate
from .llm import model_from_config
from .runtime import Runtime
from .store import Store

log = logging.getLogger("dongdong.web")


class WebState:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.store = Store(config.HOME / "dongdong.db")
        if not work_gate.current():
            self.store.stop_orphan_runs()
        self.workspace = Path(cfg["workspace"]).expanduser().resolve()
        self.model = None
        self.error = ""
        self._task: asyncio.Task | None = None
        self._run_id = ""
        self.reload()

    def reload(self) -> None:
        if not self.workspace.is_dir():
            self.model = None
            self.error = f"工作区不存在: {self.workspace}"
            return
        try:
            self.model = model_from_config(self.cfg)
            self.error = ""
        except ValueError as exc:
            self.model = None
            self.error = str(exc)

    @property
    def busy(self) -> bool:
        return bool(self._task and not self._task.done())

    async def start(self, session_id: str, text: str) -> str:
        if self.busy:
            raise RuntimeError("上一个任务还在跑")
        if self.model is None:
            raise ValueError(self.error or "模型未配置")
        owner = f"web-{uuid.uuid4().hex[:8]}"
        if not work_gate.acquire(owner):
            raise RuntimeError("另一个任务还在跑")
        loop = asyncio.get_running_loop()
        started: asyncio.Future[str] = loop.create_future()

        def on_event(kind: str, data: dict) -> None:
            if kind == "run_start" and not started.done():
                started.set_result(data["run_id"])

        async def work() -> None:
            try:
                runtime = Runtime(
                    self.store,
                    self.model,
                    self.workspace,
                    max_steps=int(self.cfg["runtime"]["max_steps"]),
                    history_messages=int(self.cfg["runtime"]["history_messages"]),
                )
                await runtime.run(session_id, text, on_event=on_event)
            finally:
                work_gate.release(owner)
                if self._task is asyncio.current_task():
                    self._task = None
                    self._run_id = ""

        task = asyncio.create_task(work())
        self._task = task
        task.add_done_callback(self._consume)
        try:
            done, _ = await asyncio.wait(
                {started, task}, timeout=2, return_when=asyncio.FIRST_COMPLETED
            )
            if started in done:
                run_id = started.result()
                if not task.done():
                    self._run_id = run_id
                return run_id
            if task in done:
                await task
            raise RuntimeError("任务启动超时")
        finally:
            if not started.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                started.cancel()

    def _consume(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            log.warning("Web 任务失败: %s", exc)

    def cancel(self, run_id: str) -> bool:
        if self._run_id != run_id or not self._task or self._task.done():
            return False
        self._task.cancel()
        return True

    async def close(self) -> None:
        if self._task and not self._task.done():
            task = self._task
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.store.close()
