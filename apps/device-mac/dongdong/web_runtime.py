"""Web 会话任务的启动、取消和有序退出。"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace

from . import config, work_gate
from .approvals import Approvals
from .llm import model_from_config
from .store import Store
from .task_scheduler import TaskManager

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
        self.manager = TaskManager(
            self.store,
            cfg=cfg,
            model=self.model,
            max_workers=1,
            approve=self.approvals.request,
        )
        self.manager.recover_runs()

    def voice_text(self, _connection: str, role: str, text: str) -> None:
        if not text.strip():
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
            if hasattr(self, "manager"):
                self.manager.model = self.model
                self.manager.task_executor.model = self.model
        except ValueError as exc:
            self.model = None
            self.error = str(exc)

    @property
    def busy(self) -> bool:
        return bool(
            self._run_id
            and self.manager.status(self._run_id)["run"]
            and self.manager.status(self._run_id)["run"]["status"] == "running"
        )

    async def start(self, session_id: str, text: str, cwd: str | None = None) -> str:
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
        if work_gate.current():
            raise RuntimeError("另一个任务还在跑")
        self.task_session = session_id
        if (
            self._run_id
            and self.manager.status(self._run_id)["run"]
            and self.manager.status(self._run_id)["run"]["status"] == "running"
        ):
            raise RuntimeError("上一个任务还在跑")
        run_id = self.manager.submit(session_id, text, str(workspace), channel="web")
        self._run_id = self._last_run = run_id
        return run_id

    def _consume(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            log.warning("Web 任务失败: %s", exc)

    def cancel(self, run_id: str) -> bool:
        if self._run_id != run_id:
            return False
        self.manager.stop(run_id)
        return True

    async def stop_active(self) -> None:
        if self._run_id:
            self.manager.stop(self._run_id)

    async def close(self) -> None:
        await self.stop_active()
        self.manager.close()
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
            if work_gate.current():
                return "另一个任务还在跑，先停止或等它结束。"
            if self.state.model is None:
                raise ValueError(self.state.error or "模型未配置")
            sid = self.state.task_session or self.state.store.create_session(text[:24], "linx")
            rid = await self.state.start(sid, text, cwd)
            self.last = rid
            await asyncio.to_thread(self.state.manager.wait, rid, wait)
            return self.result(rid)

        return asyncio.run_coroutine_threadsafe(submit(), self.loop).result(timeout=wait + 3)

    def result(self, job_id: str = "") -> str:
        rid = job_id or self.state._run_id or self.state._last_run or self.last
        run = self.state.store.get_run(rid) if rid else None
        if not run:
            return f"找不到任务 {rid}。" if rid else "还没有跑过任务。"
        status = {
            "running": "还在跑",
            "done": "完成",
            "stopped": "已停止",
            "canceled": "已取消",
            "failed": "失败",
            "error": "失败",
        }
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

    def task_status(self, run_id: str = "") -> str:
        rid = run_id or self.state._run_id or self.state._last_run
        return (
            json.dumps(self.state.manager.status(rid), ensure_ascii=False)
            if rid
            else "还没有任务。"
        )

    def task_stop(self, run_id: str = "") -> str:
        rid = run_id or self.state._run_id or self.state._last_run
        if not rid:
            return "还没有任务。"
        return json.dumps(self.state.manager.stop(rid), ensure_ascii=False)

    def task_resume(self, run_id: str = "") -> str:
        rid = run_id or self.state._last_run
        return json.dumps(self.state.manager.resume(rid), ensure_ascii=False)

    def close(self) -> None:
        asyncio.run_coroutine_threadsafe(self.state.stop_active(), self.loop).result(timeout=3)
