"""任务设备工具，沿用同一个注册表和绑定宿主。"""

from .tools import _code_stop, _peek_service, tool


@tool(
    "self.task.status",
    "查看任务 Run、Goal、Item、Attempt 的统一状态。",
    props={"run_id": {"type": "string", "description": "任务号，不填就是最近一次"}},
)
def _task_status(args: dict) -> str:
    service = _peek_service()
    if service and hasattr(service, "task_status"):
        return service.task_status(args.get("run_id") or "")
    return "任务状态只在 Web/内置 Runtime 服务中可用。"


@tool(
    "self.task.goal_preview",
    "只读预览任务 Goal 验收结果，不改变任务状态。",
    props={"run_id": {"type": "string", "description": "任务号，不填就是最近一次"}},
)
def _task_goal_preview(args: dict) -> str:
    service = _peek_service()
    if service and hasattr(service, "task_goal_preview"):
        return service.task_goal_preview(args.get("run_id") or "")
    return "Goal 验收预览只在 Web/内置 Runtime 服务中可用。"


@tool(
    "self.task.stop",
    "停止任务并有序收口。",
    props={"run_id": {"type": "string", "description": "任务号，不填就是最近一次"}},
)
def _task_stop(args: dict) -> str:
    service = _peek_service()
    if service and hasattr(service, "task_stop"):
        return service.task_stop(args.get("run_id") or "")
    return _code_stop(args)


@tool(
    "self.task.resume",
    "恢复一个可恢复的暂停或失败任务。",
    props={"run_id": {"type": "string", "description": "任务号"}},
    required=["run_id"],
)
def _task_resume(args: dict) -> str:
    service = _peek_service()
    if service and hasattr(service, "task_resume"):
        return service.task_resume(args.get("run_id") or "")
    return "当前入口暂不支持恢复任务。"


@tool(
    "self.task.repair",
    "为失败 Item 追加受限修复任务并重新验收 Goal。",
    props={
        "run_id": {"type": "string", "description": "任务号"},
        "item_id": {"type": "string", "description": "失败 Item，不填自动选择"},
        "instruction": {"type": "string", "description": "修复指令"},
    },
    required=["run_id"],
)
def _task_repair(args: dict) -> str:
    service = _peek_service()
    if service and hasattr(service, "task_repair"):
        return service.task_repair(
            args.get("run_id") or "", args.get("item_id") or "", args.get("instruction") or ""
        )
    if service and getattr(service, "state", None) and hasattr(service.state, "manager"):
        import json

        return json.dumps(
            service.state.manager.repair(
                args.get("run_id") or "",
                args.get("item_id") or None,
                args.get("instruction") or None,
            ),
            ensure_ascii=False,
        )
    return "当前入口暂不支持修复任务。"
