"""MCP over 灵矽 WebSocket：JSON-RPC 2.0 分发。字段含义见 docs/linx-protocol.md。"""

from __future__ import annotations

import logging

from . import tools

log = logging.getLogger("dongdong.mcp")

PROTOCOL_VERSION = "2024-11-05"


def handle(payload: dict) -> dict | None:
    """收一个 MCP payload，返回要发回去的 payload；通知类消息返回 None。"""
    method = payload.get("method")
    rid = payload.get("id")
    if rid is None:
        return None  # notifications/initialized 之类，不用回

    if method == "initialize":
        return _ok(
            rid,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "dongdong-mac", "version": "0.1.0"},
            },
        )
    if method == "tools/list":
        return _ok(rid, {"tools": [t.describe() for t in tools.TOOLS.values()], "nextCursor": None})
    if method == "tools/call":
        params = payload.get("params") or {}
        name = params.get("name") or ""
        if name not in tools.TOOLS:
            return _err(rid, -32601, f"未知工具: {name}")
        try:
            return _ok(rid, tools.call(name, params.get("arguments") or {}))
        except Exception as exc:
            log.exception("tools/call 失败: %s", name)
            return _err(rid, -32603, str(exc))
    if method == "ping":
        return _ok(rid, {})
    return _err(rid, -32601, f"未知方法: {method}")


def _ok(rid, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _err(rid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}
