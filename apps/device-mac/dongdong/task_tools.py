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
