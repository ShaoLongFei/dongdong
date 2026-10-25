"""任务执行器适配层；调度器只依赖 AttemptResult。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from threading import Event

from . import agent
from .runtime import Runtime
from .task_model import AttemptResult


class TaskPlanner:
    def plan(self, text: str, cwd: str, agent_name: str = "builtin") -> list[dict]:
        return [{"instruction": text.strip(), "cwd": cwd, "agent": agent_name or "builtin"}]


class TaskExecutor:
    def __init__(self, cfg: dict, store, model=None, runner: Callable | None = None, approve=None):
        self.cfg, self.store, self.model = cfg, store, model
        self.runner, self.approve, self.approval_loop = runner, approve, None

    async def _approval(self, *args) -> bool:
        if self.approve is None:
            raise ValueError("没有可用的审批入口")
        if self.approval_loop is None:
            return await self.approve(*args)
        pending = asyncio.run_coroutine_threadsafe(self.approve(*args), self.approval_loop)
        return await asyncio.wrap_future(pending)

    async def _run(self, runtime: Runtime, item: dict, run: dict, cancel: Event) -> str:
        events = self.store.events(run["id"])
        user_id = next(
            (e["data"].get("user_message_id") for e in events if e["type"] == "run_start"), None
        )
        task = asyncio.create_task(
            runtime.run(run["session_id"], item["instruction"], run_id=run["id"], user_id=user_id)
        )
        try:
            while not task.done():
                if cancel.is_set():
                    task.cancel()
                    break
                await asyncio.wait({task}, timeout=0.05)
            return await task
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def execute(self, item: dict, run: dict, cancel_event: Event) -> AttemptResult:
        if cancel_event.is_set():
            return AttemptResult("stopped", error="用户停止")
        if self.runner:
            return self.runner(item, run, cancel_event)
        try:
            if item["agent"] == "builtin":
                if self.model is None:
                    return AttemptResult("blocked", error="builtin 模型未配置")
                runtime = Runtime(
                    self.store,
                    self.model,
                    Path(item["cwd"] or run.get("workspace") or self.cfg["workspace"]),
                    max_steps=int(self.cfg["runtime"]["max_steps"]),
                    history_messages=int(self.cfg["runtime"]["history_messages"]),
                    cfg=self.cfg,
                    approve=self._approval if self.approve else None,
                    instruction=self._item_context(item, run),
                    isolated=True,
                )
                text = asyncio.run(self._run(runtime, item, run, cancel_event))
                return AttemptResult("done", output=text)
            result = agent.run(self.cfg, item["instruction"], item["agent"], item["cwd"] or None)
            return AttemptResult("done", output=result)
        except asyncio.CancelledError:
            return AttemptResult("stopped", error="用户停止")
        except (ValueError, PermissionError) as exc:
            return AttemptResult("blocked", error=str(exc), error_kind="blocked")
        except Exception as exc:  # noqa: BLE001 外部 agent/网络错误可重试
            return AttemptResult("retryable_error", error=str(exc), error_kind="worker")

    def _item_context(self, item: dict, run: dict) -> str:
        deps = self.store.item_dependencies(item["id"])
        lines = [
            f"\n父目标：{run.get('goal_text', '')}",
            f"\n当前子任务：{item.get('instruction', '')}",
        ]
        if item.get("acceptance"):
            lines.append(f"\n验收标准：{item['acceptance']}")
        if deps:
            lines.append("\n前置子任务结果：")
            lines.extend(f"- {dep['id']}: {dep.get('result', '')}" for dep in deps)
        lines.append("\n只处理当前子任务，完成后用简短文字报告结果。")
        return "".join(lines)
