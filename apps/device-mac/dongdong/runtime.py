"""Agent loop：模型流 → 工具调用 → 工具结果 → 再问模型，直到没有工具调用。"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable

from . import builtin_tools
from .llm import ModelConfig, ToolCall, stream_chat
from .store import Store

SYSTEM_PROMPT = """你是咚咚，一个跑在用户 Mac 上的通用 agent。
文件工具只能操作工作区内的路径；shell 是有意保留的逃生口，但优先在工作区内操作。
工作区：{workspace}

规则：
- 先读再改；不确定就查。
- 修改文件用 edit_file（旧文本必须唯一），新建/覆盖用 write_file。
- 跑命令用 shell，命令要短，不要用交互式命令。
- 删除、覆盖、git push、安装软件等不可逆操作，先不执行，直接说明需要用户确认。
- 不要编造工具结果；工具报错就换方案。
- 回答简洁，说清楚做了什么、结果是什么。"""

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
    ):
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
            self.store.add_event(run_id, "run_start", {"session_id": session_id})
            emit("run_start", {"run_id": run_id})
            for _ in range(self.max_steps):
                messages = self._messages(session_id, user_id)
                text = ""
                calls: list[ToolCall] = []
                async for event in self._stream(self.model, messages, builtin_tools.TOOL_SPECS):
                    if event.text:
                        text += event.text
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
                        result = await builtin_tools.call_tool(call.name, args, self.tool_ctx)
                        result = result[:12_000]
                        ok = not result.startswith(("执行失败:", "未知工具"))
                    except Exception as exc:
                        result = f"执行失败: {type(exc).__name__}: {exc}"
                        ok = False
                    ms = int((time.monotonic() - started) * 1000)
                    self.store.append_message(session_id, "tool", result, tool_call_id=call.id)
                    self.store.record_tool_call(run_id, call.name, args, result, ok, ms)
                    self.store.add_event(
                        run_id,
                        "tool_result",
                        {"name": call.name, "result": result, "ok": ok, "ms": ms},
                    )
                    emit(
                        "tool_result",
                        {"name": call.name, "result": result, "ok": ok, "ms": ms},
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
