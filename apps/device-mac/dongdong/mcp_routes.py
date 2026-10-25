"""外部 MCP 的显式配置和连接测试；敏感值只写不读。"""

from urllib.parse import urlparse

from fastapi import APIRouter, Body, HTTPException

from . import config, work_gate
from .mcp_client import SERVER_NAME, MCPTools


def _validate(name: str, body: dict, previous: dict) -> dict:
    if not SERVER_NAME.fullmatch(name):
        raise HTTPException(400, "服务名仅支持 1–32 位字母、数字、下划线和连字符")
    allowed = {"transport", "enabled", "command", "args", "env", "url", "headers"}
    if set(body) - allowed:
        raise HTTPException(400, "未知配置字段")
    transport = body.get("transport")
    if not isinstance(transport, str) or transport not in {"stdio", "streamable_http", "sse"}:
        raise HTTPException(400, "不支持的传输类型")
    if not isinstance(body.get("enabled"), bool):
        raise HTTPException(400, "enabled 必须为布尔值")
    for field in ("command", "url"):
        if field in body and not isinstance(body[field], str):
            raise HTTPException(400, f"{field} 必须为字符串")
    result = dict(body)
    for field in ("env", "headers"):
        value = body.get(field, previous.get(field, {}))
        if not isinstance(value, dict) or any(
            not isinstance(k, str) or not isinstance(v, str) for k, v in value.items()
        ):
            raise HTTPException(400, f"{field} 必须为字符串键值对象")
        result[field] = value
    args = body.get("args", [])
    if not isinstance(args, list) or any(not isinstance(arg, str) for arg in args):
        raise HTTPException(400, "args 必须为字符串数组")
    result["args"] = args
    if transport == "stdio":
        if not isinstance(body.get("command"), str) or not body["command"].strip():
            raise HTTPException(400, "stdio 需要 command")
    else:
        url = body.get("url")
        if not isinstance(url, str):
            raise HTTPException(400, "需要 HTTP(S) URL")
        try:
            parsed = urlparse(url)
            hostname = parsed.hostname
            parsed.port
        except ValueError:
            raise HTTPException(400, "URL 主机或端口格式不正确") from None
        if parsed.scheme not in {"http", "https"}:
            raise HTTPException(400, "需要 HTTP(S) URL")
        if not hostname or parsed.username is not None or parsed.password is not None:
            raise HTTPException(400, "URL 需要主机且不能包含用户名密码")
    return result


def _public(name: str, server: dict) -> dict:
    return {
        "name": name,
        "transport": server["transport"],
        "enabled": server.get("enabled", False),
        "command": server.get("command", ""),
        "args": server.get("args", []),
        "url": server.get("url", ""),
        "env_keys": list(server.get("env", {})),
        "header_keys": list(server.get("headers", {})),
    }


def router(cfg: dict) -> APIRouter:
    routes = APIRouter(prefix="/api/mcp")

    def acquire():
        if not work_gate.acquire("mcp-settings"):
            raise HTTPException(409, "任务或设置操作进行中")

    @routes.get("")
    async def listing():
        return {"servers": [_public(n, s) for n, s in cfg.get("mcp_servers", {}).items()]}

    @routes.put("/{name}")
    async def save(name: str, body: dict = Body(...)):
        acquire()
        try:
            servers = dict(cfg.get("mcp_servers", {}))
            servers[name] = _validate(name, body, servers.get(name, {}))
            updated = {**cfg, "mcp_servers": servers}
            config.save(updated)
            cfg["mcp_servers"] = servers
            return {"ok": True}
        finally:
            work_gate.release("mcp-settings")

    @routes.delete("/{name}")
    async def delete(name: str):
        acquire()
        try:
            servers = dict(cfg.get("mcp_servers", {}))
            if name not in servers:
                raise HTTPException(404, "服务不存在")
            del servers[name]
            config.save({**cfg, "mcp_servers": servers})
            cfg["mcp_servers"] = servers
            return {"ok": True}
        finally:
            work_gate.release("mcp-settings")

    @routes.post("/{name}/test")
    async def test(name: str):
        acquire()
        try:
            server = cfg.get("mcp_servers", {}).get(name)
            if server is None:
                raise HTTPException(404, "服务不存在")
            async with MCPTools({name: {**server, "enabled": True}}) as external:
                tools = [
                    {"name": original, "description": spec["function"]["description"]}
                    for spec, (_, original) in zip(external.specs, external._tools.values())
                ]
                result = {"tools": tools}
                if external.errors:
                    result["error"] = "; ".join(external.errors)
                return result
        finally:
            work_gate.release("mcp-settings")

    return routes
