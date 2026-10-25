"""任务执行器适配层；调度器只依赖 AttemptResult。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from threading import Event

from . import agent
from .runtime import Runtime
from .task_model import AttemptResult


class TaskPlanner:
    def plan(self, text: str, cwd: str, agent_name: str = "builtin") -> list[dict]:
        return [{"instruction": text.strip(), "cwd": cwd, "agent": agent_name or "builtin"}]


class TaskExecutor:
    def __init__(self, cfg: dict, store, model=None, runner: Callable | None = None, approve=None):
        self.cfg, self.store, self.model, self.runner, self.approve = (
            cfg,
            store,
            model,
            runner,
            approve,
        )

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
                    item["cwd"] or run.get("workspace") or self.cfg["workspace"],
                    max_steps=int(self.cfg["runtime"]["max_steps"]),
                    history_messages=int(self.cfg["runtime"]["history_messages"]),
                    cfg=self.cfg,
                    approve=self.approve,
                )
                text = asyncio.run(runtime.run(run["session_id"], item["instruction"]))
                return AttemptResult("done", output=text)
            result = agent.run(self.cfg, item["instruction"], item["agent"], item["cwd"] or None)
            return AttemptResult("done", output=result)
        except (ValueError, PermissionError) as exc:
            return AttemptResult("blocked", error=str(exc), error_kind="blocked")
        except Exception as exc:  # noqa: BLE001 外部 agent/网络错误可重试
            return AttemptResult("retryable_error", error=str(exc), error_kind="worker")
