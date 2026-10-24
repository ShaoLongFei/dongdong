"""OpenAI 兼容的流式聊天客户端。只做这一种协议，不抽 provider。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import httpx


@dataclass
class ModelConfig:
    base_url: str
    api_key: str
    model: str
    temperature: float = 0.2
    max_tokens: int = 4096


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass
class StreamEvent:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict | None = None


def parse_data_line(line: str) -> dict | None:
    if not line.startswith("data:"):
        return None
    raw = line[5:].strip()
    if not raw or raw == "[DONE]":
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def merge_tool_calls(acc: dict[int, dict], deltas: list[dict]) -> None:
    for delta in deltas:
        index = int(delta.get("index") or 0)
        item = acc.setdefault(index, {"id": "", "name": "", "arguments": ""})
        if delta.get("id"):
            item["id"] = delta["id"]
        fn = delta.get("function") or {}
        if fn.get("name"):
            item["name"] = fn["name"]
        if fn.get("arguments"):
            item["arguments"] += fn["arguments"]


def finish_tool_calls(acc: dict[int, dict]) -> list[ToolCall]:
    return [
        ToolCall(
            id=item["id"] or f"call_{index}",
            name=item["name"],
            arguments=item["arguments"] or "{}",
        )
        for index, item in sorted(acc.items())
        if item["name"]
    ]


async def stream_chat(
    cfg: ModelConfig, messages: list[dict], tools: list[dict]
) -> AsyncIterator[StreamEvent]:
    payload: dict = {
        "model": cfg.model,
        "messages": messages,
        "stream": True,
        "temperature": cfg.temperature,
        "max_tokens": cfg.max_tokens,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    headers = {"Authorization": f"Bearer {cfg.api_key}", "Content-Type": "application/json"}
    url = cfg.base_url.rstrip("/") + "/chat/completions"
    acc: dict[int, dict] = {}
    usage: dict | None = None

    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=15.0)) as client:
        async with client.stream("POST", url, json=payload, headers=headers) as resp:
            if resp.status_code >= 400:
                body = (await resp.aread()).decode("utf-8", "replace")[:2000]
                raise RuntimeError(f"模型请求失败 HTTP {resp.status_code}: {body}")
            async for line in resp.aiter_lines():
                if line.startswith("data:") and line[5:].strip() == "[DONE]":
                    break
                obj = parse_data_line(line)
                if obj is None:
                    continue
                if obj.get("usage"):
                    usage = obj["usage"]
                choices = obj.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                if delta.get("content"):
                    yield StreamEvent(text=delta["content"])
                if delta.get("tool_calls"):
                    merge_tool_calls(acc, delta["tool_calls"])
            calls = finish_tool_calls(acc)
            if calls:
                yield StreamEvent(tool_calls=calls)
            if usage:
                yield StreamEvent(usage=usage)
