"""结构化任务规划：模型只生成 Item DAG，规划器负责边界校验。"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .llm import stream_chat
from .task_model import ItemSpec


@dataclass(frozen=True)
class PlanResult:
    items: list[ItemSpec]
    error: str = ""
    used_fallback: bool = False


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)
_MAX_RESPONSE = 50_000


class TaskPlanner:
    """调用无工具模型生成最多 ``max_items`` 个可执行 Item。"""

    def __init__(self, model, cfg: dict, stream_fn=stream_chat, max_items: int = 8):
        self.model = model
        self.cfg = cfg or {}
        self.stream_fn = stream_fn
        self.max_items = max(1, int(max_items))

    def plan(self, text: str, cwd: str) -> PlanResult:
        original = str(text or "").strip()
        fallback_cwd = str(cwd or self.cfg.get("workspace") or "")
        try:
            raw = self._generate(original, fallback_cwd)
            value = self._decode(raw)
            items = self._validate(value, fallback_cwd)
            return PlanResult(items)
        except Exception as exc:  # noqa: BLE001 模型输出和边界错误都安全回退
            return PlanResult(
                [ItemSpec(instruction=original, cwd=fallback_cwd, agent="builtin")],
                error=str(exc) or "规划失败",
                used_fallback=True,
            )

    def _generate(self, text: str, cwd: str) -> str:
        messages = [
            {
                "role": "system",
                "content": (
                    "你是任务规划器。只返回 JSON，不要 Markdown、解释或工具调用。"
                    '格式为 {"items":[{"id":"...","instruction":"...",'
                    '"acceptance":"...","agent":"builtin","cwd":"...",'
                    '"depends_on":["其他 item id"]}]}。'
                    f"最多生成 {self.max_items} 个 Item；所有 cwd 必须在工作区内。"
                ),
            },
            {
                "role": "user",
                "content": f"目标：{text}\n工作目录：{cwd}",
            },
        ]
        result = self.stream_fn(self.model, messages, tools=[])
        return self._collect(result)

    @staticmethod
    def _collect(result: Any) -> str:
        if isinstance(result, str):
            return result[:_MAX_RESPONSE]

        async def consume(value: Any) -> str:
            chunks: list[str] = []
            if hasattr(value, "__aiter__"):
                async for event in value:
                    chunks.append(TaskPlanner._event_text(event))
            else:
                if inspect.isawaitable(value):
                    value = await value
                if hasattr(value, "__aiter__"):
                    async for event in value:
                        chunks.append(TaskPlanner._event_text(event))
                elif isinstance(value, str):
                    chunks.append(value)
                elif isinstance(value, Iterable):
                    chunks.extend(TaskPlanner._event_text(event) for event in value)
                else:
                    chunks.append(TaskPlanner._event_text(value))
            return "".join(chunks)[:_MAX_RESPONSE]

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(consume(result))

        # plan() 是同步边界；在已有事件循环（例如 async smoke）中用短线程收集流。
        box: list[str] = []
        failure: list[BaseException] = []

        def run() -> None:
            try:
                box.append(asyncio.run(consume(result)))
            except BaseException as exc:  # noqa: BLE001 将模型错误交给外层回退
                failure.append(exc)

        thread = threading.Thread(target=run, name="dongdong-planner-stream")
        thread.start()
        thread.join()
        if failure:
            raise failure[0]
        return box[0] if box else ""

    @staticmethod
    def _event_text(event: Any) -> str:
        if isinstance(event, str):
            return event
        if isinstance(event, dict):
            return str(event.get("text") or event.get("content") or "")
        return str(getattr(event, "text", "") or "")

    @staticmethod
    def _decode(raw: str) -> Any:
        source = str(raw or "").strip()
        if not source:
            raise ValueError("模型没有返回规划 JSON")
        match = _FENCE_RE.search(source)
        if match:
            source = match.group(1).strip()
        try:
            return json.loads(source)
        except json.JSONDecodeError as exc:
            # 容忍模型在 JSON 前后附带一句话，但不接受截断或多个对象。
            starts = [index for index in (source.find("{"), source.find("[")) if index >= 0]
            if not starts:
                raise ValueError(f"规划 JSON 无效: {exc.msg}") from exc
            try:
                value, end = json.JSONDecoder().raw_decode(source[min(starts) :])
            except json.JSONDecodeError as inner:
                raise ValueError(f"规划 JSON 无效: {inner.msg}") from inner
            if source[min(starts) + end :].strip():
                raise ValueError("规划 JSON 后存在额外内容")
            return value

    def _validate(self, value: Any, fallback_cwd: str) -> list[ItemSpec]:
        if isinstance(value, dict):
            value = value.get("items")
        if not isinstance(value, list) or not value:
            raise ValueError("规划必须包含非空 items 数组")
        if len(value) > self.max_items:
            raise ValueError(f"Item 数量超过上限 {self.max_items}")

        allowed_agents = {"builtin", *(self.cfg.get("agents") or {}).keys()}
        ids: set[str] = set()
        normalized: list[ItemSpec] = []
        for entry in value:
            if not isinstance(entry, dict):
                raise ValueError("Item 必须是对象")
            item_id = self._required_string(entry, "id")
            if item_id in ids:
                raise ValueError(f"Item ID 重复: {item_id}")
            ids.add(item_id)
            instruction = self._required_string(entry, "instruction")
            agent_name = entry.get("agent", "builtin")
            if not isinstance(agent_name, str) or not agent_name.strip():
                raise ValueError(f"Item {item_id} 的 agent 无效")
            agent_name = agent_name.strip()
            if agent_name not in allowed_agents:
                raise ValueError(f"未知 agent: {agent_name}")
            item_cwd = entry.get("cwd", fallback_cwd)
            if not isinstance(item_cwd, str) or not item_cwd.strip():
                raise ValueError(f"Item {item_id} 的 cwd 无效")
            item_cwd = self._check_cwd(item_cwd, fallback_cwd)
            dependencies = entry.get("depends_on", ())
            if dependencies is None:
                dependencies = ()
            if not isinstance(dependencies, (list, tuple)) or any(
                not isinstance(dep, str) or not dep.strip() for dep in dependencies
            ):
                raise ValueError(f"Item {item_id} 的 depends_on 无效")
            deps = tuple(dep.strip() for dep in dependencies)
            if item_id in deps:
                raise ValueError(f"Item {item_id} 不能依赖自身")
            normalized.append(
                ItemSpec(
                    id=item_id,
                    instruction=instruction,
                    acceptance=self._optional_string(entry, "acceptance"),
                    agent=agent_name,
                    cwd=item_cwd,
                    priority=self._integer(entry, "priority", 0),
                    max_attempts=self._integer(entry, "max_attempts", 2),
                    depends_on=deps,
                )
            )
        for item in normalized:
            missing = set(item.depends_on) - ids
            if missing:
                raise ValueError(f"Item {item.id} 依赖不存在: {', '.join(sorted(missing))}")
        self._assert_acyclic(normalized)
        return normalized

    def _check_cwd(self, raw: str, fallback_cwd: str) -> str:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = Path(fallback_cwd).expanduser() / path
        path = path.resolve()
        roots = [fallback_cwd, self.cfg.get("workspace"), *(self.cfg.get("projects") or [])]
        roots = [Path(root).expanduser().resolve() for root in roots if root]
        if not roots:
            roots = [Path(fallback_cwd).expanduser().resolve()]
        if not any(path == root or path.is_relative_to(root) for root in roots):
            raise ValueError(f"cwd 越出工作区: {raw}")
        return str(path)

    @staticmethod
    def _required_string(entry: dict, key: str) -> str:
        value = entry.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Item 缺少非空 {key}")
        return value.strip()

    @staticmethod
    def _optional_string(entry: dict, key: str) -> str:
        value = entry.get(key, "")
        if not isinstance(value, str):
            raise ValueError(f"{key} 必须是字符串")
        return value.strip()

    @staticmethod
    def _integer(entry: dict, key: str, default: int) -> int:
        value = entry.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{key} 必须是整数")
        return value

    @staticmethod
    def _assert_acyclic(items: list[ItemSpec]) -> None:
        edges = {item.id: set(item.depends_on) for item in items}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visiting:
                raise ValueError("Item 依赖存在环")
            if node in visited:
                return
            visiting.add(node)
            for dependency in edges[node]:
                visit(dependency)
            visiting.remove(node)
            visited.add(node)

        for node in edges:
            visit(node)
