"""每次运行独立连接外部 MCP；传输与协议由官方 SDK 管理。"""

from __future__ import annotations

import asyncio
import os
import re
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

from .config import expand

SERVER_NAME = re.compile(r"[A-Za-z0-9_-]{1,32}\Z")


class MCPTools:
    def __init__(self, servers: dict, connect_timeout: float = 15, call_timeout: float = 180):
        self.servers = servers
        self.connect_timeout = connect_timeout
        self.call_timeout = call_timeout
        self.specs = []
        self.errors = []
        self._tools = {}
        self._stack = AsyncExitStack()

    async def __aenter__(self):
        await self._stack.__aenter__()
        try:
            for name, server in self.servers.items():
                if not server.get("enabled", False):
                    continue
                stack = AsyncExitStack()
                try:
                    if not SERVER_NAME.fullmatch(name):
                        raise ValueError("invalid name")
                    async with asyncio.timeout(self.connect_timeout):
                        session = await self._connect(stack, server)
                        await session.initialize()
                        discovered = []
                        cursor = None
                        while True:
                            result = await session.list_tools(cursor=cursor)
                            discovered.extend(result.tools)
                            cursor = result.nextCursor
                            if not cursor:
                                break
                    self._stack.push_async_callback(stack.aclose)
                    for index, tool in enumerate(discovered):
                        model_name = f"mcp_{name}_{index}"
                        self._tools[model_name] = (session, tool.name)
                        self.specs.append(
                            {
                                "type": "function",
                                "function": {
                                    "name": model_name,
                                    "description": f"[{name}] {tool.name}: {tool.description or ''}",
                                    "parameters": tool.inputSchema,
                                },
                            }
                        )
                except BaseException as exc:
                    try:
                        await stack.aclose()
                    except Exception:
                        # SDK 清理失败不能覆盖原始取消，也不能阻止连接其它服务。
                        pass
                    if not isinstance(exc, Exception):
                        raise
                    self.errors.append(f"{name}: 连接或发现失败 ({type(exc).__name__})")
            return self
        except BaseException:
            try:
                await self._stack.aclose()
            except Exception:
                pass
            raise

    async def __aexit__(self, *exc):
        try:
            return await self._stack.__aexit__(*exc)
        except Exception as error:
            self.errors.append(f"连接清理失败 ({type(error).__name__})")
            return False

    async def _connect(self, stack: AsyncExitStack, server: dict):
        transport = server["transport"]
        if transport == "stdio":
            params = StdioServerParameters(
                command=expand(server["command"]),
                args=[expand(arg) for arg in server.get("args", [])],
                env={key: expand(value) for key, value in server.get("env", {}).items()},
            )
            errlog = stack.enter_context(open(os.devnull, "w"))
            streams = await stack.enter_async_context(stdio_client(params, errlog=errlog))
        else:
            headers = {key: expand(value) for key, value in server.get("headers", {}).items()}
            if transport == "streamable_http":
                streams = await stack.enter_async_context(
                    streamablehttp_client(
                        expand(server["url"]),
                        headers=headers,
                        timeout=self.connect_timeout,
                        sse_read_timeout=self.call_timeout,
                    )
                )
            elif transport == "sse":
                streams = await stack.enter_async_context(
                    sse_client(
                        expand(server["url"]),
                        headers=headers,
                        timeout=self.connect_timeout,
                        sse_read_timeout=self.call_timeout,
                    )
                )
            else:
                raise ValueError("invalid transport")
        return await stack.enter_async_context(ClientSession(streams[0], streams[1]))

    async def call(self, model_tool_name: str, args: dict) -> str:
        target = self._tools.get(model_tool_name)
        if target is None:
            return "未知工具"
        session, name = target
        try:
            async with asyncio.timeout(self.call_timeout):
                result = await session.call_tool(name, args)
            text = "\n".join(
                item.text if item.type == "text" else item.model_dump_json()
                for item in result.content
            )
            return f"执行失败: {text}" if result.isError else text
        except Exception as exc:
            # SDK 异常可能带 URL、headers 或子进程环境，避免传给模型。
            return f"执行失败: 外部工具调用失败 ({type(exc).__name__})"
