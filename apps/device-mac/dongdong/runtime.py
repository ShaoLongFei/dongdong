"""Agent loop：模型流 → 工具调用 → 工具结果 → 再问模型，直到没有工具调用。"""

from __future__ import annotations

import asyncio
import copy
import json
import time
from collections.abc import Callable

from . import builtin_tools, config, skills
from .llm import ModelConfig, ToolCall, stream_chat
from .mcp_client import MCPTools
from .progress import PLAN_TOOL, _plan  # noqa: F401
from .store import Store

SYSTEM_PROMPT = """你是咚咚，一个跑在用户 Mac 上的通用 agent。
文件工具只能操作工作区内的路径；shell 是有意保留的逃生口，但优先在工作区内操作。
工作区：{workspace}

规则：
- 先读再改；不确定就查。
- 修改文件用 edit_file（旧文本必须唯一），新建/覆盖用 write_file。
- 跑命令用 shell，命令要短，不要用交互式命令。
- 删除、git push、安装软件等不可逆操作，先说明需要用户确认；若有审批界面，等待批准后执行。
- 不要编造工具结果；工具报错就换方案。
- 多步骤任务先用 update_plan 给出简短步骤清单，切换步骤时更新状态；简单问答无需计划。
- 计划只记录实际进度，未执行的步骤不能标 completed；完成后再给最终交付结果。
- 回答简洁，说清楚做了什么、结果是什么。"""

READ_SKILL = {
    "type": "function",
    "function": {
        "name": "read_skill",
        "description": "读取已启用技能的完整 SKILL.md 指令，需要时先读再做。",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
}

Emit = Callable[[str, dict], None]


def _trim(rows: list[dict], limit: int, keep_id: int | None = None) -> list[dict]:
    # 窗口是软限制：当前 run 的 user 消息必须留下，否则长任务会丢目标。
    if limit <= 0:
        limit = len(rows)
    start = max(0, len(rows) - limit)
    if keep_id is not None:
        for index, row in enumerate(rows):
            if row["id"] == keep_id:
                start = min(start, index)
                break
    rows = rows[start:]
    while rows and rows[0]["role"] == "tool":
        rows.pop(0)
    out: list[dict] = []
    index = 0
    while index < len(rows):
        row = rows[index]
        if row["role"] == "assistant" and row["tool_calls"]:
            need = {call["id"] for call in row["tool_calls"]}
            end = index + 1
            got: set[str] = set()
            while end < len(rows) and rows[end]["role"] == "tool":
                got.add(rows[end]["tool_call_id"])
                end += 1
            if need <= got:
                out.append(row)
                # 只带上这次调用自己的结果：混进来的 tool 消息对不上 tool_calls 会被 400
                out.extend(part for part in rows[index + 1 : end] if part["tool_call_id"] in need)
                index = end
                continue
            index += 1
            continue
        if row["role"] == "tool":
            index += 1
            continue
        out.append(row)
        index += 1
    return out


class Runtime:
    def __init__(
        self,
        store: Store,
        model: ModelConfig,
        workspace,
        *,
        max_steps: int = 20,
        history_messages: int = 30,
        stream_fn=stream_chat,
        cfg: dict | None = None,
        approve=None,
    ):
        self.cfg = copy.deepcopy(cfg if cfg is not None else config.load())
        self.approve = approve
        self.store = store
        self.model = model
        self.workspace = workspace
        self.max_steps = max_steps
        self.history_messages = history_messages
        self._stream = stream_fn
        self.tool_ctx = builtin_tools.ToolContext(workspace=workspace)
        self.system_prompt = SYSTEM_PROMPT.format(workspace=workspace)

    def _messages(self, session_id: str, keep_id: int | None = None) -> list[dict]:
        rows = _trim(self.store.messages(session_id), self.history_messages, keep_id)
        out: list[dict] = [{"role": "system", "content": self.system_prompt}]
        for row in rows:
            if row["role"] == "tool":
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": row["tool_call_id"],
                        "content": row["content"],
                    }
                )
            elif row["role"] == "assistant" and row["tool_calls"]:
                out.append(
                    {
                        "role": "assistant",
                        "content": row["content"] or None,
                        "tool_calls": row["tool_calls"],
                    }
                )
            else:
                out.append({"role": row["role"], "content": row["content"]})
        return out

    async def run(self, session_id: str, user_text: str, on_event: Emit | None = None) -> str:
        emit: Emit = on_event or (lambda kind, data: None)
        run_id = ""
        usage: dict = {}
        try:
            run_id = self.store.create_run(session_id)
            user_id = self.store.append_message(session_id, "user", user_text)
            self.store.add_event(
                run_id, "run_start", {"session_id": session_id, "user_message_id": user_id}
            )
            emit("run_start", {"run_id": run_id})

            def record(kind: str, data: dict) -> None:
                self.store.add_event(run_id, kind, data)
                emit(kind, data)

            self.system_prompt = SYSTEM_PROMPT.format(workspace=self.workspace)
            available = skills.list_skills(self.cfg)
            enabled = [s for s in available if s["enabled"]]
            if enabled:
                self.system_prompt += (
                    "\n可用技能（需要时先调用 read_skill 读取完整指令）：\n"
                    + "\n".join(f"- {s['name']}: {s['description']}" for s in enabled)
                )
            async with MCPTools(self.cfg.get("mcp_servers", {})) as external:
                for error in external.errors:
                    record("mcp_error", {"message": error})
                specs = [*builtin_tools.TOOL_SPECS, PLAN_TOOL, READ_SKILL, *external.specs]
                for _ in range(self.max_steps):
                    self.store.add_event(run_id, "model_start", {})
                    emit("model_start", {})
                    messages = self._messages(session_id, user_id)
                    text = ""
                    calls: list[ToolCall] = []
                    async for event in self._stream(self.model, messages, specs):
                        if event.text:
                            text += event.text
                            self.store.add_event(run_id, "text", {"delta": event.text})
                            emit("text", {"delta": event.text})
                        if event.tool_calls:
                            calls = event.tool_calls
                        if event.usage:
                            usage = event.usage
                    if not calls:
                        self.store.append_message(session_id, "assistant", text)
                        self.store.add_event(run_id, "done", {"text": text})
                        self.store.finish_run(run_id, "done", usage=usage)
                        emit("done", {"text": text})
                        return text

                    self.store.append_message(
                        session_id,
                        "assistant",
                        text,
                        tool_calls=[
                            {
                                "id": call.id,
                                "type": "function",
                                "function": {"name": call.name, "arguments": call.arguments},
                            }
                            for call in calls
                        ],
                    )
                    for call in calls:
                        started = time.monotonic()
                        self.store.add_event(
                            run_id,
                            "tool_call",
                            {"id": call.id, "name": call.name, "arguments": call.arguments},
                        )
                        emit(
                            "tool_call",
                            {"id": call.id, "name": call.name, "arguments": call.arguments},
                        )
                        args: dict = {}
                        try:
                            parsed = json.loads(call.arguments or "{}")
                            if not isinstance(parsed, dict):
                                raise ValueError("参数必须是对象")
                            args = parsed
                            needs_approval = call.name in (
                                "shell",
                                "write_file",
                                "edit_file",
                            ) or call.name.startswith("mcp_")
                            if self.cfg.get("tool_policy") == "ask" and needs_approval:
                                if self.approve is None:
                                    raise ValueError(
                                        "此入口不能审批操作，请在 Web 工作台允许或切换为自动执行"
                                    )
                                if not await self.approve(run_id, call.id, call.name, args, record):
                                    raise ValueError("用户拒绝了本次操作，请停止该操作并说明结果")
                            if call.name == "update_plan":
                                plan = _plan(args)
                                self.store.add_event(run_id, "plan", plan)
                                emit("plan", plan)
                                result = "计划已更新。请继续执行，步骤切换时更新计划。"
                            elif call.name == "read_skill":
                                result = skills.read_skill(args.get("name", ""), self.cfg)
                            elif call.name.startswith("mcp_"):
                                result = await external.call(call.name, args)
                            else:
                                result = await builtin_tools.call_tool(
                                    call.name, args, self.tool_ctx
                                )
                            result = (
                                result[:32_768] if call.name == "read_skill" else result[:12_000]
                            )
                            ok = not result.startswith(("执行失败:", "未知工具"))
                            if call.name == "shell":
                                ok = result.startswith("exit_code=0\n")
                        except Exception as exc:
                            result = f"执行失败: {type(exc).__name__}: {exc}"
                            ok = False
                        ms = int((time.monotonic() - started) * 1000)
                        self.store.append_message(session_id, "tool", result, tool_call_id=call.id)
                        self.store.record_tool_call(run_id, call.name, args, result, ok, ms)
                        self.store.add_event(
                            run_id,
                            "tool_result",
                            {
                                "id": call.id,
                                "name": call.name,
                                "result": result,
                                "ok": ok,
                                "ms": ms,
                            },
                        )
                        emit(
                            "tool_result",
                            {
                                "id": call.id,
                                "name": call.name,
                                "result": result,
                                "ok": ok,
                                "ms": ms,
                            },
                        )
                raise RuntimeError(f"超过最大步数 {self.max_steps}")
        except asyncio.CancelledError:
            if run_id:
                self.store.finish_run(run_id, "stopped", error="用户停止")
                self.store.add_event(run_id, "stopped", {})
                emit("stopped", {})
            raise
        except Exception as exc:
            if run_id:
                self.store.finish_run(run_id, "error", error=str(exc))
                self.store.add_event(run_id, "error", {"message": str(exc)})
            emit("error", {"message": str(exc)})
            raise
