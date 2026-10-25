"""Web 会话任务的启动、取消和有序退出。"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from types import SimpleNamespace

from . import config, work_gate
from .approvals import Approvals
from .llm import model_from_config
from .runtime import Runtime
from .store import Store

log = logging.getLogger("dongdong.web")


class WebState:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.approvals = Approvals()
        self.store = Store(config.HOME / "dongdong.db")
        if not work_gate.current():
            self.store.stop_orphan_runs()
        self.workspace = Path(cfg["workspace"]).expanduser().resolve()
        self.model = None
        self.error = ""
        self._task: asyncio.Task | None = None
        self._run_id = ""
        self._last_run = ""
        self.task_session = ""
        self.voice_session = ""
        self.reload()
        from .meeting import Meeting

        self.meeting = Meeting(self)

    def voice_text(self, _connection: str, role: str, text: str) -> None:
        if not text.strip():
            return
        if role == "user" and self.meeting.voice(text):
            return
        # 平台可能每轮结束就断链，连接 ID 不能作为用户会话的边界。
        if not self.voice_session:
            self.voice_session = self.store.create_session("语音对话", "linx-voice")
        self.store.append_message(self.voice_session, role, text)

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

    async def start(
        self,
        session_id: str,
        text: str,
        cwd: str | None = None,
        *,
        meeting_options=None,
        finished=None,
        observer=None,
    ) -> str:
        if self.meeting.db.active() and meeting_options is None:
            raise ValueError("代码修改模式中请通过会议入口讨论、整理和确认")
        if self.busy:
            raise RuntimeError("上一个任务还在跑")
        if self.model is None:
            raise ValueError(self.error or "模型未配置")
        workspace = self.workspace
        if cwd:
            path = Path(cwd).expanduser()
            workspace = (path if path.is_absolute() else workspace / path).resolve()
            if not workspace.is_dir() or not workspace.is_relative_to(self.workspace):
                raise ValueError(f"目录必须存在且在工作区内: {self.workspace}")
        owner = f"web-{uuid.uuid4().hex[:8]}"
        if not work_gate.acquire(owner):
            raise RuntimeError("另一个任务还在跑")
        self.task_session = session_id
        loop = asyncio.get_running_loop()
        started: asyncio.Future[str] = loop.create_future()

        def on_event(kind: str, data: dict) -> None:
            if observer:
                observer(kind, data)
            if kind == "run_start" and not started.done():
                started.set_result(data["run_id"])

        async def work() -> None:
            status, error = "done", ""
            try:
                runtime = Runtime(
                    self.store,
                    self.model,
                    workspace,
                    max_steps=int(self.cfg["runtime"]["max_steps"]),
                    history_messages=int(self.cfg["runtime"]["history_messages"]),
                    cfg=self.cfg,
                    approve=self.approvals.request,
                    **(meeting_options or {}),
                )
                await runtime.run(session_id, text, on_event=on_event)
            except asyncio.CancelledError:
                status, error = "stopped", "用户停止；保留部分改动"
                raise
            except Exception as exc:
                status, error = "error", str(exc)
                raise
            finally:
                try:
                    if finished and started.done() and not started.cancelled():
                        await finished(status, started.result(), error)
                except asyncio.CancelledError:
                    if finished and started.done() and not started.cancelled():
                        await finished("stopped", started.result(), "收尾时停止；保留部分改动")
                    raise
                except Exception as exc:
                    log.warning("会议收尾失败: %s", exc)
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
                self._last_run = run_id
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

    async def stop_active(self) -> None:
        if self._task and not self._task.done():
            task = self._task
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def close(self) -> None:
        for task in list(self.meeting.pending):
            task.cancel()
        await asyncio.gather(*self.meeting.pending, return_exceptions=True)
        await self.stop_active()
        self.store.close()


class WebAgent:
    """MCP 在工作线程同步等待；实际任务始终在 Web 的事件循环中执行。"""

    def __init__(self, state: WebState):
        self.state = state
        self.loop = asyncio.get_running_loop()
        self.last = ""

    def start(self, instruction: str, cwd: str | None = None, wait_seconds: int = 8) -> str:
        text = (instruction or "").strip()
        if not text:
            raise ValueError("instruction 不能为空")
        wait = max(1, min(int(wait_seconds or 8), 8))

        async def submit():
            meeting = self.state.meeting.mcp_status()
            if meeting:
                return meeting
            if work_gate.current():
                return "另一个任务还在跑，先停止或等它结束。"
            if self.state.model is None:
                raise ValueError(self.state.error or "模型未配置")
            sid = self.state.task_session or self.state.store.create_session(text[:24], "linx")
            rid = await self.state.start(sid, text, cwd)
            self.last = rid
            task = self.state._task
            if task:
                await asyncio.wait({task}, timeout=wait)
            return self.result(rid)

        return asyncio.run_coroutine_threadsafe(submit(), self.loop).result(timeout=wait + 3)

    def meeting_status(self) -> str | None:
        return self.state.meeting.mcp_status()

    def result(self, job_id: str = "") -> str:
        meeting = self.meeting_status()
        if meeting and not job_id:
            return meeting
        rid = job_id or self.state._run_id or self.state._last_run or self.last
        run = self.state.store.get_run(rid) if rid else None
        if not run:
            return f"找不到任务 {rid}。" if rid else "还没有跑过任务。"
        status = {"running": "还在跑", "done": "完成", "stopped": "已停止", "error": "失败"}
        events = self.state.store.events(rid)
        text = "".join(
            event["data"].get("delta", "") for event in events if event["type"] == "text"
        )
        return f"任务 {rid} {status[run['status']]}：\n{run['error'] or text[-2000:] or '(还没有输出)'}"

    def stop(self, job_id: str = "") -> str:
        rid = job_id or self.state._run_id or self.state._last_run or self.last

        async def cancel():
            if not self.state.cancel(rid):
                return f"任务 {rid} 已结束或不存在。"
            await self.state.stop_active()
            return f"已停止任务 {rid}。"

        return asyncio.run_coroutine_threadsafe(cancel(), self.loop).result(timeout=3)

    def current(self):
        rid = self.state._run_id
        return SimpleNamespace(id=rid) if rid else None

    def snapshot(self) -> dict | None:
        rid = self.state._run_id or self.state._last_run or self.last
        run = self.state.store.get_run(rid) if rid else None
        if not run:
            return None
        return {
            "id": rid,
            "agent": "builtin",
            "status": run["status"],
            "running": run["status"] == "running",
            "output": self.result(rid),
        }

    def close(self) -> None:
        asyncio.run_coroutine_threadsafe(self.state.stop_active(), self.loop).result(timeout=3)
