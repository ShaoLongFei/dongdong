"""内置工具：shell、读、写、改、列目录、glob、grep。文件工具限制在工作区内；shell 是显式逃逸口。"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import os
import re
import signal
import time
from dataclasses import dataclass
from pathlib import Path

MAX_READ = 200_000
MAX_OUTPUT = 12_000
MAX_ITEMS = 200
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__"}
DANGEROUS = ("sudo ", "rm -rf /", "mkfs", "shutdown", "reboot", "diskutil erase", ":(){")


@dataclass
class ToolContext:
    workspace: Path
    timeout_s: int = 30

    def __post_init__(self):
        self.workspace = self.workspace.resolve()


TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "shell",
            "description": "在工作区执行 shell 命令，返回退出码和合并后的输出。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的命令"},
                    "timeout_s": {"type": "number", "description": "超时秒数，默认 30"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取工作区内的文本文件。",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "写入工作区内的文本文件，自动建父目录。",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "把文件里唯一出现的一段旧文本替换成新文本。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old": {"type": "string"},
                    "new": {"type": "string"},
                },
                "required": ["path", "old", "new"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "列出工作区内目录的内容。",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": "按 glob pattern 查找工作区内的文件。",
            "parameters": {
                "type": "object",
                "properties": {"pattern": {"type": "string"}},
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "用正则搜索工作区内的文件内容。",
            "parameters": {
                "type": "object",
                "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}},
                "required": ["pattern"],
            },
        },
    },
]


def resolve_path(workspace: Path, raw: str) -> Path:
    if not raw or "\x00" in raw:
        raise ValueError("路径为空或含非法字符")
    path = Path(os.path.expanduser(raw))
    path = path if path.is_absolute() else workspace / path
    path = path.resolve()
    if path != workspace and not path.is_relative_to(workspace):
        raise ValueError(f"路径越界: {raw}")
    return path


async def _drain(stream, limit: int) -> bytes:
    """边读边丢，只保留最后 limit 字节（尾部错误信息最重要），避免巨量输出打爆内存。"""
    data = bytearray()
    while True:
        chunk = await stream.read(8192)
        if not chunk:
            return bytes(data)
        data.extend(chunk)
        del data[: max(0, len(data) - limit)]


def _kill_group(proc) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGKILL)


async def _collect(proc, reader, timeout: float) -> tuple[str | None, str]:
    """等 sh 退出再收输出：asyncio 的 proc.wait() 会被后台孙进程占着的管道拖住，只能轮询 returncode。"""
    deadline = time.monotonic() + timeout
    while proc.returncode is None and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    if proc.returncode is None:
        _kill_group(proc)
        await proc.wait()
        reader.cancel()
        return f"命令超时（{timeout:g}s）", ""
    try:
        text = (await asyncio.wait_for(reader, timeout=0.5)).decode("utf-8", "replace")
        return None, text
    except TimeoutError:
        _kill_group(proc)
        await proc.wait()
        reader.cancel()
        return "命令超时（后台子进程仍在运行，已终止）", ""


async def _shell(args: dict, ctx: ToolContext) -> str:
    command = str(args.get("command") or "").strip()
    if not command:
        raise ValueError("command 不能为空")
    if any(word in command for word in DANGEROUS):
        raise ValueError("命令被安全策略拒绝")
    timeout = max(0.2, min(float(args.get("timeout_s") or ctx.timeout_s), 300.0))
    proc = await asyncio.create_subprocess_shell(
        command,
        cwd=ctx.workspace,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,  # 独立进程组，超时/后台子进程可整组杀掉
    )
    reader = asyncio.create_task(_drain(proc.stdout, MAX_OUTPUT))
    try:
        error, text = await _collect(proc, reader, timeout)
    except asyncio.CancelledError:
        _kill_group(proc)
        await proc.wait()
        reader.cancel()
        raise
    if error:
        return error
    return f"exit_code={proc.returncode}\n{text}"


def _read(args: dict, ctx: ToolContext) -> str:
    path = resolve_path(ctx.workspace, str(args["path"]))
    if path.stat().st_size > MAX_READ:
        raise ValueError("文件超过 200KB")
    return path.read_text(encoding="utf-8", errors="replace")


def _write(args: dict, ctx: ToolContext) -> str:
    path = resolve_path(ctx.workspace, str(args["path"]))
    text = str(args.get("content") or "")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return f"已写入 {path.relative_to(ctx.workspace)}（{len(text)} 字符）"


def _edit(args: dict, ctx: ToolContext) -> str:
    path = resolve_path(ctx.workspace, str(args["path"]))
    old, new = str(args["old"]), str(args["new"])
    if not old:
        raise ValueError("old 不能为空")
    if path.stat().st_size > MAX_READ:
        raise ValueError("文件超过 200KB")
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise ValueError(f"old 出现 {count} 次，必须唯一")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return f"已修改 {path.relative_to(ctx.workspace)}"


def _list(args: dict, ctx: ToolContext) -> str:
    path = resolve_path(ctx.workspace, str(args.get("path") or "."))
    if not path.is_dir():
        raise ValueError("不是目录")
    items = [child.name + ("/" if child.is_dir() else "") for child in sorted(path.iterdir())]
    return "\n".join(items[:MAX_ITEMS]) or "(空目录)"


def _glob(args: dict, ctx: ToolContext) -> str:
    pattern = str(args["pattern"])
    if Path(pattern).is_absolute():
        raise ValueError("pattern 必须是工作区内的相对路径")
    found: list[str] = []
    for path in ctx.workspace.glob(pattern):
        resolved = path.resolve()
        if resolved == ctx.workspace or resolved.is_relative_to(ctx.workspace):
            found.append(str(resolved.relative_to(ctx.workspace)))
        if len(found) >= MAX_ITEMS:
            break
    return "\n".join(sorted(found)) or "(无匹配)"


def _grep(args: dict, ctx: ToolContext) -> str:
    regex = re.compile(str(args["pattern"]))
    root = resolve_path(ctx.workspace, str(args.get("path") or "."))
    hits: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in SKIP_DIRS]
        for filename in filenames:
            path = (Path(dirpath) / filename).resolve()
            if not path.is_relative_to(ctx.workspace):
                continue
            try:
                if path.stat().st_size > 1_000_000:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    hits.append(f"{path.relative_to(ctx.workspace)}:{lineno}: {line.strip()[:300]}")
                    if len(hits) >= 100:
                        return "\n".join(hits)
    return "\n".join(hits) or "(无匹配)"


_HANDLERS = {
    "shell": _shell,
    "read_file": _read,
    "write_file": _write,
    "edit_file": _edit,
    "list_dir": _list,
    "glob": _glob,
    "grep": _grep,
}


async def call_tool(name: str, args: dict, ctx: ToolContext) -> str:
    handler = _HANDLERS.get(name)
    if handler is None:
        return f"未知工具: {name}"
    try:
        result = handler(args, ctx)
        if inspect.isawaitable(result):
            result = await result
        return str(result)
    except Exception as exc:
        return f"执行失败: {type(exc).__name__}: {exc}"
