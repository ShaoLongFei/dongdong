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
from .story import StorySession, set_story_mode, story_command
from .story_store import StoryStore
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
        self._tasks = {}
        self._runs = {}
        self._last_runs = {}
        self.stories = {}
        self.task_session = ""
        self.current_project = None
        self._project_tasks = {}
        self.loop = None
        self.reload()
        from .meeting import Meeting

        self.meeting = Meeting(self)
        self.manager = TaskManager(
            self.store, cfg=cfg, model=self.model, approve=self.approvals.request, max_workers=4
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
        run = self.store.get_run(rid)
        if run and self._runs.get(run["session_id"]) == rid:
            self._runs.pop(run["session_id"], None)

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
        sid = self.task_session
        session = self.store.get_session(sid) if sid else None
        if not session or session["archived_at"]:
            sid = self.new_session("新对话", "web", self.current_project)
        self.store.append_message(sid, role, text, source="voice")
        command = self._story_command(text) if role == "user" else None
        if command:
            if self.loop and self.loop.is_running():
                self.loop.create_task(self.story_mode(command == "start", sid)).add_done_callback(
                    self._consume
                )
            return
        if role == "user" and self.story.status == "recording":
            self.story.accept_text(text)
            return
        if role == "user":
            self.meeting.voice(text)

    _story_command = staticmethod(story_command)

    @property
    def story(self):
        sid = self.task_session
        if sid not in self.stories:
            self.stories[sid] = StorySession(
                model=self.model, store=StoryStore(config.HOME / "stories")
            )
        return self.stories[sid]

    async def story_mode(self, enabled: bool, sid: str | None = None) -> dict:
        return await set_story_mode(self, enabled, sid)

    def reload(self) -> None:
        try:
            self.model = model_from_config(self.cfg)
            self.error = ""
            if hasattr(self, "manager"):
                self.manager.model = self.manager.task_executor.model = self.model
            for story in self.stories.values():
                story.model = self.model
        except ValueError as exc:
            self.model = None
            self.error = str(exc)

    @property
    def _run_id(self) -> str:
        return self._runs.get(self.task_session, "")

    @property
    def _last_run(self) -> str:
        rows = self.store.list_runs(self.task_session, limit=1) if self.task_session else []
        return self._last_runs.get(self.task_session) or (rows[0]["id"] if rows else "")

    def session_busy(self, sid: str) -> bool:
        return bool(work_gate.current(sid))

    @property
    def busy(self) -> bool:
        return self.session_busy(self.task_session)

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
        story = self.stories.get(session_id)
        if story and story.status == "recording":
            raise RuntimeError("本对话故事模式录音中，请先结束讲故事")
        if self.meeting.db.active(session_id) and meeting_options is None:
            raise ValueError("代码修改模式中请通过会议入口讨论、整理和确认")
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
        if work_gate.current(session_id):
            raise RuntimeError("本对话任务还在跑")
        if meeting_options is None:
            rid = self.manager.submit(session_id, text, str(workspace), channel="web")
            self._runs[session_id] = self._last_runs[session_id] = rid
            return rid
        owner = f"web-{uuid.uuid4().hex[:8]}"
        if not work_gate.acquire(owner, session_id):
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
                    if self._tasks.get(session_id) is asyncio.current_task():
                        self._tasks.pop(session_id, None)
                        self._runs.pop(session_id, None)

        task = asyncio.create_task(work())
        self._tasks[session_id] = task
        task.add_done_callback(self._consume)
        try:
            done, _ = await asyncio.wait(
                {started, task}, timeout=2, return_when=asyncio.FIRST_COMPLETED
            )
            if started in done:
                run_id = started.result()
                self._last_runs[session_id] = run_id
                if not task.done():
                    self._runs[session_id] = run_id
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
        run = self.store.get_run(run_id)
        if not run or self._runs.get(run["session_id"]) != run_id:
            return False
        task = self._tasks.get(run["session_id"])
        if task and not task.done():
            task.cancel()
        else:
            self.manager.stop(run_id)
        return True

    async def stop_active(self, run_id: str = "") -> None:
        rid = run_id or self._run_id
        if not self.cancel(rid):
            return
        run = self.store.get_run(rid)
        task = self._tasks.get(run["session_id"])
        if task:
            await asyncio.gather(task, return_exceptions=True)
        else:
            await asyncio.to_thread(self.manager.wait, rid, 10)

    async def close(self) -> None:
        for task in list(self.meeting.pending):
            task.cancel()
        await asyncio.gather(*self.meeting.pending, return_exceptions=True)
        for task in list(self._tasks.values()):
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        for rid in list(self._runs.values()):
            await self.stop_active(rid)
        await asyncio.gather(*(story.close() for story in self.stories.values()))
        await asyncio.to_thread(self.manager.close)
        self.store.close()
