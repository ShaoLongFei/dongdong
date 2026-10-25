"""工具注册表 + 实现。同一份工具给灵矽 MCP 和本地 HTTP bridge 用。"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from . import agent, config, memory, screen, state

log = logging.getLogger("dongdong.tools")

_service = None
_service_lock = threading.Lock()


def _backend(cfg: dict) -> str:
    backend = cfg.get("code_backend", "cli")
    if backend not in {"cli", "builtin"}:
        raise ValueError(f"未知 code_backend: {backend}，只能是 cli 或 builtin")
    return backend


def _get_service(cfg: dict):
    global _service
    from .agent_service import AgentService

    with _service_lock:
        if _service is None:
            _service = AgentService(cfg)
        return _service


def _peek_service():
    with _service_lock:
        return _service


def bind_service(service) -> None:
    global _service
    with _service_lock:
        if _service is not None:
            raise RuntimeError("agent service 已启动，请先关闭旧服务")
        _service = service


def service_snapshot():
    service = _peek_service()
    return service.snapshot() if service else None


def close_service() -> None:
    global _service
    with _service_lock:
        service, _service = _service, None
    if service:
        service.close()


@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    fn: Callable[[dict], str]

    def describe(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.schema,
            "type": 0,
        }


TOOLS: dict[str, Tool] = {}


def tool(name: str, description: str, props: dict | None = None, required: list[str] | None = None):
    def register(fn: Callable[[dict], str]) -> Callable[[dict], str]:
        TOOLS[name] = Tool(
            name=name,
            description=description,
            schema={"type": "object", "properties": props or {}, "required": required or []},
            fn=fn,
        )
        return fn

    return register


def call(name: str, arguments: dict) -> dict:
    """MCP tools/call 的返回体。未知工具抛 KeyError，让上层回 -32601。"""
    registered = TOOLS[name]
    started = time.monotonic()
    try:
        text = registered.fn(arguments or {})
        result = {"content": [{"type": "text", "text": str(text)}], "isError": False}
    except Exception as exc:  # 工具内部任何异常都变成语音能读的一句话
        log.exception("工具 %s 失败", name)
        result = {"content": [{"type": "text", "text": f"执行失败: {exc}"}], "isError": True}
    state.record_call(name, arguments or {}, int((time.monotonic() - started) * 1000), result)
    return result


# ---------------------------------------------------------------- 状态


@tool("self.system.status", "查看咚咚当前状态：用哪个 agent、工作目录、有没有任务在跑")
def _status(args: dict) -> str:
    cfg = config.load()
    backend = _backend(cfg)
    service = _peek_service()
    if service and hasattr(service, "execution_workspace"):
        cfg = {**cfg, "workspace": service.execution_workspace()}
        cfg.update(projects=[cfg["workspace"]], cwd=cfg["workspace"])
    if backend == "builtin":
        job = service.current() if service else None
        return f"agent=builtin 工作区={cfg['workspace']} 运行中任务={job.id if job else '无'}"
    job = agent.current()
    return (
        f"agent={cfg['agent']}（可选 {', '.join(cfg['agents'])}）"
        f" 工作目录={agent.resolve_dir(cfg, None)}"
        f" 运行中任务={job.id + '(' + job.agent + ')' if job else '无'}"
    )


# ---------------------------------------------------------------- 编程


@tool(
    "self.code.run",
    "让本地 code agent 执行一句编程任务并返回结果。后端由配置 code_backend 决定："
    "cli 用 codex/claude/deepseek，builtin 用内置 Runtime。"
    "任务通常要几十秒，指令要具体，比如「在 dongdong 项目里给 README 加一段安装说明」。",
    props={
        "instruction": {"type": "string", "description": "要 agent 做什么，自然语言，尽量具体"},
        "agent": {"type": "string", "description": "CLI 后端的 agent 名字；builtin 忽略"},
        "cwd": {"type": "string", "description": "项目目录，必须在工作区/允许的项目路径下"},
        "wait_seconds": {
            "type": "integer",
            "description": "最多等多少秒；builtin 默认 8，语音固定最多 8",
        },
    },
    required=["instruction"],
)
def _code_run(args: dict) -> str:
    cfg = config.load()
    if _backend(cfg) == "builtin":
        wait = min(int(args["wait_seconds"]) if args.get("wait_seconds") else 8, 8)
        return _get_service(cfg).start(args["instruction"], args.get("cwd"), wait)
    service = _peek_service()
    if service and hasattr(service, "execution_workspace"):
        root = service.execution_workspace()
        cfg = {**cfg, "projects": [root], "cwd": root}
    return agent.run(
        cfg,
        args["instruction"],
        args.get("agent"),
        args.get("cwd"),
        int(args["wait_seconds"]) if args.get("wait_seconds") else None,
    )


@tool(
    "self.code.result",
    "查看编程任务的输出。任务跑太久没等到结果时用这个查。",
    props={"job_id": {"type": "string", "description": "任务号，不填就是最近一次"}},
)
def _code_result(args: dict) -> str:
    if _backend(config.load()) == "builtin":
        service = _peek_service()
        return service.result(args.get("job_id") or "") if service else "还没有跑过任务。"
    return agent.result(args.get("job_id") or "")


@tool(
    "self.code.stop",
    "停止正在跑的编程任务。",
    props={"job_id": {"type": "string", "description": "任务号，不填就停最近的"}},
)
def _code_stop(args: dict) -> str:
    if _backend(config.load()) == "builtin":
        service = _peek_service()
        return service.stop(args.get("job_id") or "") if service else "没有可停的任务。"
    return agent.stop(args.get("job_id") or "")


# ---------------------------------------------------------------- 投屏


@tool("self.screen.list", "列出局域网里可以投屏的 AirPlay 设备（电视、Apple TV）")
def _screen_list(args: dict) -> str:
    found = screen.targets(3.0)
    if not found:
        return "没扫到可投屏设备，确认电视开着、和 Mac 在同一个 Wi-Fi。"
    return "可投屏：" + "；".join(f"{t['name']}（{t['address']}）" for t in found)


@tool(
    "self.screen.mirror",
    "把 Mac 屏幕镜像投到指定电视。target 用 self.screen.list 里的名字。",
    props={"target": {"type": "string", "description": "目标设备名字，比如「卧室」"}},
    required=["target"],
)
def _screen_mirror(args: dict) -> str:
    return screen.mirror(config.load(), args["target"])


@tool("self.screen.stop", "停止当前的屏幕镜像")
def _screen_stop(args: dict) -> str:
    return screen.stop(config.load())


# ---------------------------------------------------------------- 记忆

_MEMORY_SCOPE = {
    "type": "string",
    "enum": ["project", "global"],
    "description": "默认 project：当前明确选择的项目，无项目时为全局；global：跨项目记忆",
}


def _memory_user(args: dict) -> str:
    scope = args.get("scope", "project")
    if scope not in {"project", "global"}:
        raise ValueError("scope 必须为 project 或 global")
    service = _peek_service()
    project = service.memory_project() if service and hasattr(service, "memory_project") else None
    return memory.namespace(config.load()["device_id"], project if scope == "project" else None)


@tool(
    "self.memory.search",
    "搜索全局及当前项目各最多 5 条记忆，供回答前参考；scope=global 仅查全局。",
    props={
        "query": {"type": "string", "description": "要查找的主题或问题"},
        "scope": _MEMORY_SCOPE,
    },
    required=["query"],
)
def _memory_search(args: dict) -> str:
    user_id = _memory_user(args)
    global_id = _memory_user({"scope": "global"})
    scope = "project" if user_id != global_id else "global"
    results = [{**r, "scope": scope} for r in memory.search(args["query"], user_id)]
    if user_id != global_id:
        results = [
            {**r, "scope": "global"} for r in memory.search(args["query"], global_id)
        ] + results
    text = "\n".join(f"[{r['scope']}] {r['id']}: {r.get('memory', '')}" for r in results)
    return text or "没有找到相关记忆。"


@tool(
    "self.memory.save",
    "保存用户明确要求记住的内容、稳定偏好或长期项目事实。不要保存临时请求、密码、token 或凭证。",
    props={"text": {"type": "string", "description": "要保存的事实或偏好"}, "scope": _MEMORY_SCOPE},
    required=["text"],
)
def _memory_save(args: dict) -> str:
    result = memory.save(args["text"], _memory_user(args))
    saved = result.get("results", [])
    if not saved:
        return "没有提取到可保存的新记忆。"
    return "记忆已保存：\n" + "\n".join(
        f"{item.get('id', '')}: {item.get('memory', '')}" for item in saved
    )


@tool(
    "self.memory.list",
    "列出当前项目的记忆及 ID；scope=global 列出跨项目记忆。",
    props={"scope": _MEMORY_SCOPE},
)
def _memory_list(args: dict) -> str:
    items = memory.list_(_memory_user(args))
    if not items:
        return "当前没有已保存的记忆。"
    return "\n".join(f"{item['id']}: {item.get('memory', '')}" for item in items)


@tool(
    "self.memory.delete",
    "按 ID 删除当前项目指定的记忆；scope=global 删除全局记忆。只删除用户明确指定的 ID。",
    props={
        "memory_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "要删除的记忆 ID 列表",
        },
        "scope": _MEMORY_SCOPE,
    },
    required=["memory_ids"],
)
def _memory_delete(args: dict) -> str:
    deleted = memory.delete(args["memory_ids"], _memory_user(args))
    return "已删除记忆：" + ", ".join(deleted)
