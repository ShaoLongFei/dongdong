"""灵矽同步 MCP 调用与 Web 事件循环之间的桥接。"""

import asyncio
import json
from types import SimpleNamespace


class WebAgent:
    """MCP 在工作线程同步等待；实际任务始终在 Web 的事件循环中执行。"""

    def __init__(self, state):
        self.state = state
        self.loop = asyncio.get_running_loop()
        state.activate()

    def start(self, instruction: str, cwd: str | None = None, wait_seconds: int = 8) -> str:
        text = (instruction or "").strip()
        if not text:
            raise ValueError("instruction 不能为空")
        wait = max(1, min(int(wait_seconds or 8), 8))

        async def submit():
            meeting = self.state.meeting.mcp_status()
            if meeting:
                return meeting
            if self.state.busy:
                return "另一个任务还在跑，先停止或等它结束。"
            if self.state.model is None:
                raise ValueError(self.state.error or "模型未配置")
            sid = self.state.task_session
            session = self.state.store.get_session(sid) if sid else None
            if (
                not session
                or session["archived_at"]
                or session["project_id"] != self.state.current_project
            ):
                sid = self.state.new_session(text[:24], "linx", self.state.current_project)
            rid = await self.state.start(sid, text, cwd)
            task = self.state._tasks.get(sid)
            if task:
                await asyncio.wait({task}, timeout=wait)
            else:
                await asyncio.to_thread(self.state.manager.wait, rid, wait)
            return self.result(rid)

        return asyncio.run_coroutine_threadsafe(submit(), self.loop).result(timeout=wait + 3)

    def meeting_status(self) -> str | None:
        return self.state.meeting.mcp_status()

    def result(self, job_id: str = "") -> str:
        meeting = self.meeting_status()
        if meeting and not job_id:
            return meeting
        rid = job_id or self.state._run_id or self.state._last_run
        run = self.state.store.get_run(rid) if rid else None
        if not run:
            return f"找不到任务 {rid}。" if rid else "还没有跑过任务。"
        status = {
            "running": "还在跑",
            "done": "完成",
            "stopped": "已停止",
            "error": "失败",
            "failed": "失败",
            "canceled": "已取消",
            "paused": "已暂停",
            "timeout": "已超时",
        }
        events = self.state.store.events(rid)
        text = "".join(
            event["data"].get("delta", "") for event in events if event["type"] == "text"
        )
        return f"任务 {rid} {status.get(run['status'], run['status'])}：\n{run['error'] or text or '(还没有输出)'}"

    def stop(self, job_id: str = "") -> str:
        rid = job_id or self.state._run_id or self.state._last_run

        async def cancel():
            if rid not in self.state._runs.values():
                return f"任务 {rid} 已结束或不存在。"
            await self.state.stop_active(rid)
            return f"已停止任务 {rid}。"

        return asyncio.run_coroutine_threadsafe(cancel(), self.loop).result(timeout=3)

    def current(self):
        rid = self.state._run_id
        return SimpleNamespace(id=rid) if rid else None

    def memory_project(self) -> str | None:
        return self.state.current_project

    def execution_workspace(self) -> str:
        return str(self.state.project_workspace(self.state.current_project))

    def task_status(self, run_id: str = "") -> str:
        rid = run_id or self.state._run_id or self.state._last_run
        return (
            json.dumps(self.state.manager.status(rid), ensure_ascii=False)
            if rid
            else "还没有任务。"
        )

    def task_stop(self, run_id: str = "") -> str:
        return self.stop(run_id)

    def task_resume(self, run_id: str = "") -> str:
        rid = run_id or self.state._last_run

        async def resume():
            target = self.state.store.get_run(rid)
            if target and self.state.meeting.db.active(target["session_id"]):
                raise ValueError("请通过会议入口恢复任务")
            self.state.manager.resume(rid)
            run = self.state.store.get_run(rid)
            if run and run["status"] == "running":
                self.state._runs[run["session_id"]] = rid
                self.state._last_runs[run["session_id"]] = rid
            return self.task_status(rid)

        return asyncio.run_coroutine_threadsafe(resume(), self.loop).result(timeout=3)

    def task_repair(self, run_id: str = "", item_id: str = "", instruction: str = "") -> str:
        rid = run_id or self.state._last_run

        async def repair():
            result = self.state.manager.repair(rid, item_id or None, instruction or None)
            run = result.get("run") or {}
            if run.get("session_id"):
                self.state._runs[run["session_id"]] = rid
                self.state._last_runs[run["session_id"]] = rid
            return json.dumps(result, ensure_ascii=False)

        return asyncio.run_coroutine_threadsafe(repair(), self.loop).result(timeout=3)

    def snapshot(self) -> dict | None:
        rid = self.state._run_id or self.state._last_run
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
