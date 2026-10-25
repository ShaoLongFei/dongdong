"""OpenAI 兼容的流式聊天客户端。只做这一种协议，不抽 provider。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import httpx

from . import config


@dataclass
class ModelConfig:
    base_url: str
    api_key: str
    model: str
    temperature: float = 0.2
    max_tokens: int = 4096
    extra_body: dict = field(default_factory=dict)


def model_from_config(cfg: dict) -> ModelConfig:
    """从配置构造模型客户端参数；key 只在调用时展开。"""
    model = cfg["model"]
    api_key = config.expand(model["api_key"])
    if not api_key:
        raise ValueError(
            "没有模型 key：在 ~/.dongdong/config.json 设置 model.api_key 或导出 DEEPSEEK_API_KEY"
        )
    return ModelConfig(
        base_url=model["base_url"],
        api_key=api_key,
        model=model["model"],
        temperature=float(model["temperature"]),
        max_tokens=int(model["max_tokens"]),
        extra_body=model.get("extra_body") or {},
    )


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


def _is_model_unavailable(body: str) -> bool:
    """只对服务端明确声明 model_unavailable 的响应启用兼容回退。"""
    try:
        value = json.loads(body)
    except json.JSONDecodeError:
        return False
    if not isinstance(value, dict):
        return False
    error = value.get("error")
    if isinstance(error, dict) and (
        error.get("code") == "model_unavailable" or error.get("type") == "model_unavailable"
    ):
        return True
    return value.get("code") == "model_unavailable"


def _parse_non_stream_completion(value: dict) -> list[StreamEvent]:
    choices = value.get("choices") or []
    if not choices:
        return []
    message = choices[0].get("message") or {}
    events: list[StreamEvent] = []
    content = message.get("content")
    if content:
        events.append(StreamEvent(text=content))
    calls: list[ToolCall] = []
    for item in message.get("tool_calls") or []:
        function = item.get("function") or {}
        arguments = function.get("arguments", "{}")
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments, ensure_ascii=False)
        calls.append(
            ToolCall(
                id=item.get("id") or f"call_{len(calls)}",
                name=function.get("name", ""),
                arguments=arguments,
            )
        )
    if calls:
        events.append(StreamEvent(tool_calls=calls))
    if value.get("usage"):
        events.append(StreamEvent(usage=value["usage"]))
    return events


async def stream_chat(
    cfg: ModelConfig, messages: list[dict], tools: list[dict], transport=None
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
    if cfg.extra_body:
        payload.update(cfg.extra_body)
    headers = {"Authorization": f"Bearer {cfg.api_key}", "Content-Type": "application/json"}
    url = cfg.base_url.rstrip("/") + "/chat/completions"
    acc: dict[int, dict] = {}
    usage: dict | None = None

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(180.0, connect=15.0), transport=transport
    ) as client:
        async with client.stream("POST", url, json=payload, headers=headers) as resp:
            if resp.status_code >= 400:
                body = (await resp.aread()).decode("utf-8", "replace")[:2000]
                if resp.status_code == 400 and _is_model_unavailable(body):
                    fallback_payload = dict(payload)
                    fallback_payload["stream"] = False
                    # 部分兼容网关会把不支持的 extra_body（例如 thinking）
                    # 误报为模型不可用；兼容回退时去掉该扩展参数。
                    fallback_payload.pop("thinking", None)
                    fallback = await client.post(url, json=fallback_payload, headers=headers)
                    if fallback.status_code >= 400:
                        fallback_body = (await fallback.aread()).decode("utf-8", "replace")[:2000]
                        raise RuntimeError(
                            f"模型请求失败 HTTP {fallback.status_code}: {fallback_body}"
                        )
                    try:
                        completion = fallback.json()
                    except ValueError as exc:
                        raise RuntimeError("模型请求失败：非流式响应不是 JSON") from exc
                    if not isinstance(completion, dict):
                        raise RuntimeError("模型请求失败：非流式响应格式无效")
                    for event in _parse_non_stream_completion(completion):
                        yield event
                    return
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
