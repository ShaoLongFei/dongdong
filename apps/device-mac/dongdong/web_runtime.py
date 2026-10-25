"""Web 会话任务的启动、取消和有序退出。"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from . import config, work_gate
from .approvals import Approvals
from .llm import model_from_config
from .runtime import Runtime
from .store import Store
from .task_scheduler import TaskManager
from .web_agent import WebAgent  # noqa: F401

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
        self.current_project = None
        self._project_tasks = {}
        self.loop = None
        self.reload()
        from .meeting import Meeting

        self.meeting = Meeting(self)
        active = self.meeting.db.active()
        if active:
            self.task_session = active["sid"]
            self.current_project = self.store.get_session(active["sid"])["project_id"]
        self.manager = TaskManager(
            self.store, cfg=cfg, model=self.model, approve=self.approvals.request
        )

    def activate(self) -> None:
        if self.loop is None:
            self.loop = asyncio.get_running_loop()
            self.manager.task_executor.approval_loop = self.loop
            self.manager.on_finished = lambda rid: self.loop.call_soon_threadsafe(
                self._finished, rid
            )
            self.manager.recover_runs()

    def _finished(self, rid: str) -> None:
        if self._run_id == rid:
            self._run_id = ""

    def project_workspace(self, project_id: str | None = None) -> Path:
        project = self.store.get_project(project_id) if project_id else None
        if project_id and not project:
            raise ValueError("项目不存在")
        return Path(project["path"]).resolve() if project else self.workspace

    def set_project(self, project_id: str | None) -> None:
        if not self.project_workspace(project_id).is_dir():
            raise ValueError("项目目录不存在")
        self.current_project = project_id
        self.task_session = self._project_tasks.get(project_id, "")

    def forget_session(self, sid: str) -> None:
        for key in [key for key, value in self._project_tasks.items() if value == sid]:
            self._project_tasks.pop(key)
        if self.task_session == sid:
            self.task_session = ""

    def new_session(self, title: str, channel: str, project_id: str | None) -> str:
        sid = self.store.create_session(title, channel, project_id)
        self._project_tasks[project_id] = sid
        if project_id == self.current_project:
            self.task_session = sid
        return sid

    def voice_text(self, _connection: str, role: str, text: str) -> None:
        if not text.strip():
            return
        active = self.meeting.db.active()
        sid = active["sid"] if active else self.task_session
        session = self.store.get_session(sid) if sid else None
        if not session or session["archived_at"]:
            sid = self.new_session("新对话", "web", self.current_project)
        self.store.append_message(sid, role, text, source="voice")
        if role == "user":
            self.meeting.voice(text)

    def reload(self) -> None:
        try:
            self.model = model_from_config(self.cfg)
            self.error = ""
            if hasattr(self, "manager"):
                self.manager.model = self.manager.task_executor.model = self.model
        except ValueError as exc:
            self.model = None
            self.error = str(exc)

    @property
    def busy(self) -> bool:
        run = self.store.get_run(self._run_id) if self._run_id else None
        return bool((self._task and not self._task.done()) or (run and run["status"] == "running"))

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
        self.activate()
        session = self.store.get_session(session_id)
        if not session or session["archived_at"]:
            raise RuntimeError("会话不存在或已归档，请先恢复")
        root = self.project_workspace(session["project_id"])
        workspace = root
        if cwd:
            path = Path(cwd).expanduser()
            workspace = (path if path.is_absolute() else workspace / path).resolve()
            if not workspace.is_dir() or not workspace.is_relative_to(root):
                raise ValueError(f"目录必须存在且在工作区内: {root}")
        if not workspace.is_dir():
            raise ValueError(f"工作目录不存在: {workspace}")
        if work_gate.current():
            raise RuntimeError("另一个任务还在跑")
        self._project_tasks[session["project_id"]] = session_id
        if session["project_id"] == self.current_project:
            self.task_session = session_id
        if meeting_options is None:
            rid = self.manager.submit(session_id, text, str(workspace), channel="web")
            self._run_id = self._last_run = rid
            return rid
        owner = f"web-{uuid.uuid4().hex[:8]}"
        if not work_gate.acquire(owner):
            raise RuntimeError("另一个任务还在跑")
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
        if self._run_id != run_id or not self.busy:
            return False
        if self._task and not self._task.done():
            self._task.cancel()
        else:
            self.manager.stop(run_id)
        return True

    async def stop_active(self) -> None:
        if self._task and not self._task.done():
            task = self._task
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        elif self._run_id:
            rid = self._run_id
            self.manager.stop(rid)
            await asyncio.to_thread(self.manager.wait, rid, 10)

    async def close(self) -> None:
        for task in list(self.meeting.pending):
            task.cancel()
        await asyncio.gather(*self.meeting.pending, return_exceptions=True)
        await self.stop_active()
        await asyncio.to_thread(self.manager.close)
        self.store.close()
