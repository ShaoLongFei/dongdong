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
        listed = [t.describe() for t in tools.TOOLS.values()]
        service = tools._peek_service()
        status = (
            service.meeting_status() if service and hasattr(service, "meeting_status") else None
        )
        if status:
            # 云端不会收到本地模式变化；发现工具时带上目录，后续调用仍查询实时状态。
            for item in listed:
                if item["name"] in {"self.system.status", "self.code.run", "self.code.result"}:
                    item["description"] += f"\n当前会议上下文（操作前查询实时状态）：{status}"
        return _ok(rid, {"tools": listed, "nextCursor": None})
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
