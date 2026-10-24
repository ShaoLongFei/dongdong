"""工具注册表 + 实现。同一份工具给灵矽 MCP 和本地 HTTP bridge 用。"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from . import agent, config, screen, state

log = logging.getLogger("dongdong.tools")


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
    job = agent.current()
    return (
        f"agent={cfg['agent']}（可选 {', '.join(cfg['agents'])}）"
        f" 工作目录={agent.resolve_dir(cfg, None)}"
        f" 运行中任务={job.id + '(' + job.agent + ')' if job else '无'}"
    )


# ---------------------------------------------------------------- 编程


@tool(
    "self.code.run",
    "让 code agent 执行一句编程任务并返回结果。agent 名字来自配置，默认 codex / claude / deepseek。"
    "任务通常要几十秒，指令要具体，比如「在 dongdong 项目里给 README 加一段安装说明」。",
    props={
        "instruction": {"type": "string", "description": "要 agent 做什么，自然语言，尽量具体"},
        "agent": {"type": "string", "description": "配置里的 agent 名字，不填用默认那个"},
        "cwd": {"type": "string", "description": "项目目录，必须在允许的项目路径下"},
        "wait_seconds": {"type": "integer", "description": "最多等多少秒，默认 120"},
    },
    required=["instruction"],
)
def _code_run(args: dict) -> str:
    return agent.run(
        config.load(),
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
    return agent.result(args.get("job_id") or "")


@tool(
    "self.code.stop",
    "停止正在跑的编程任务。",
    props={"job_id": {"type": "string", "description": "任务号，不填就停最近的"}},
)
def _code_stop(args: dict) -> str:
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
