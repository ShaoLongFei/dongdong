# Agent Runtime MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让咚咚拥有一个不依赖 codex/claude CLI 的内置 agent：能读文件、改文件、跑 shell，会话和工具调用落 SQLite，终端里可对话。

**Architecture:** 新增一个独立的 agent runtime 包，和现有灵矽/面板/CLI 适配层并存。Runtime 用 asyncio 驱动 OpenAI 兼容的流式模型调用，执行内置工具，把消息、run、事件、工具调用写进 SQLite。CLI 只是一个事件消费者；后续 Web 和灵矽自定义模型端点复用同一个 Runtime。

**Tech Stack:** Python 3.12、stdlib `sqlite3`、`httpx`（已有）、`asyncio`。这个计划不新增依赖。

**Spec:** `docs/superpowers/specs/2026-10-24-general-agent-design.md`

## Global Constraints

- Python 3.12，`ruff format`，行宽 100。
- 不新增依赖；SQLite 用标准库，HTTP 用已有的 `httpx`。
- 每个新文件 ≤ 300 行。
- 现有 `linx.py`、`audio.py`、`panel.py`、`tools.py`、`agent.py` 不在这个计划里改；新 runtime 先并行存在。
- 模型协议只做 OpenAI 兼容 `/chat/completions`（base URL 可含 `/v1`；流式 + tools）。
- 本地单用户；工作区一个根目录；默认只监听 `127.0.0.1`。
- 测试放在 `tests/agent_smoke.py`。这是对 `AGENTS.md`「唯一测试文件」的显式例外：agent loop 的边界不能只靠手测。`tests/smoke.py` 保持原样，`scripts/check.sh` 三套都跑。
- 执行后补充：HTTP/SSE 传输测试拆到 `tests/agent_transport.py`，`check.sh` 现在跑三套；`agent_smoke.py` 只保留 loop/tool 边界。
- 测试不联网；模型流用假函数注入。

## Review Focus

五个最容易在真实使用中咬人的输入/条件，各自的测试放在对应任务里：

1. **路径逃逸/符号链接**：`../`、绝对路径、指向工作区外的 symlink 必须被拒绝。→ Task 3。
2. **shell 超时/巨量输出**：命令卡住或输出几十 MB 时，进程要 kill，返回要有上限，不能把内存打爆。→ Task 3。
3. **SSE 工具参数被拆包**：`arguments` 分多个 chunk 到达、非法 JSON、缺 `id` 时，不能丢工具调用或崩掉。→ Task 2 / Task 4。
4. **历史裁剪切断工具对**：裁到只剩 assistant(tool_calls) 没有对应 tool 结果时，下一次模型请求会 400。→ Task 4。
5. **取消/重启留下 running**：Ctrl+C、任务取消、进程重启后，不能有永远 `running` 的 run 或孤儿工具进程。→ Task 1 / Task 4 / Task 5。

---

## File Structure

```
apps/device-mac/dongdong/
  schema.sql          新增：SQLite 表
  store.py            新增：持久化入口（会话/消息/run/事件/工具调用）
  llm.py              新增：OpenAI 兼容流式客户端 + SSE 解析
  builtin_tools.py    新增：shell / read / write / edit / list / glob / grep
  runtime.py          新增：agent loop、事件、取消、历史裁剪
  chat_cli.py         新增：终端对话入口
  config.py           修改：model / workspace / runtime 默认值
  __main__.py         修改：加 chat 子命令
tests/
  agent_smoke.py      新增：纯 assert 的 runtime 测试
scripts/
  check.sh            修改：追加 agent_smoke
docs/
  decisions.md        修改：记录 SQLite、OpenAI-only、测试例外
```

`store.py` 只负责 SQL，`runtime.py` 只负责循环，`builtin_tools.py` 只负责工具，`llm.py` 只负责协议。这样每个文件都能在 300 行内，也方便后面 Web/语音复用。

---

### Task 1: SQLite 持久化

**Files:**
- Create: `apps/device-mac/dongdong/schema.sql`
- Create: `apps/device-mac/dongdong/store.py`
- Test: `tests/agent_smoke.py`

**Interfaces:**
- Produces: `Store(path: Path)`、`create_session(title, channel) -> str`、`list_sessions() -> list[dict]`、`get_session(sid) -> dict | None`、`append_message(...) -> int`、`messages(sid) -> list[dict]`、`create_run(sid) -> str`、`finish_run(rid, status, error, usage)`、`get_run(rid) -> dict | None`、`add_event(rid, kind, data) -> int`、`events(rid, since) -> list[dict]`、`record_tool_call(...) -> int`、`close()`。

- [ ] **Step 1: 写失败测试**

```python
# tests/agent_smoke.py
import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "device-mac"))
os.environ["DONGDONG_HOME"] = tempfile.mkdtemp(prefix="dongdong-agent-smoke-")

from dongdong.store import Store  # noqa: E402


def test_store_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "t.db"
        store = Store(path)
        sid = store.create_session("测试")
        store.append_message(sid, "user", "你好")
        store.append_message(sid, "assistant", "在")
        rid = store.create_run(sid)
        store.add_event(rid, "text", {"delta": "在"})
        store.record_tool_call(rid, "shell", {"command": "pwd"}, "ok", True, 12)
        store.finish_run(rid, "done", usage={"total_tokens": 3})
        store.close()

        store = Store(path)
        assert store.get_session(sid)["title"] == "测试"
        assert [m["content"] for m in store.messages(sid)] == ["你好", "在"]
        assert store.events(rid)[0]["type"] == "text"
        assert store.get_run(rid)["status"] == "done"
        assert store.get_run(rid)["usage"]["total_tokens"] == 3
        store.close()


def test_store_stops_orphan_runs():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "t.db"
        store = Store(path)
        sid = store.create_session("测试")
        rid = store.create_run(sid)
        store.close()

        store = Store(path)
        assert store.stop_orphan_runs() == 1
        assert store.get_run(rid)["status"] == "stopped"
        assert store.stop_orphan_runs() == 0
        store.close()
```

- [ ] **Step 2: 跑，确认失败**

Run: `.venv/bin/python tests/agent_smoke.py`

Expected: `ModuleNotFoundError: No module named 'dongdong.store'`。

- [ ] **Step 3: 写 schema 和 store**

```sql
-- apps/device-mac/dongdong/schema.sql
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  channel TEXT NOT NULL DEFAULT 'web',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  content TEXT NOT NULL DEFAULT '',
  tool_calls TEXT NOT NULL DEFAULT '[]',
  tool_call_id TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'running',
  error TEXT NOT NULL DEFAULT '',
  usage TEXT NOT NULL DEFAULT '{}',
  started_at TEXT NOT NULL,
  ended_at TEXT
);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  type TEXT NOT NULL,
  data TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  args TEXT NOT NULL DEFAULT '{}',
  result TEXT NOT NULL DEFAULT '',
  ok INTEGER NOT NULL DEFAULT 1,
  ms INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
```

```python
# apps/device-mac/dongdong/store.py
"""SQLite 持久化：会话、消息、run、事件、工具调用。"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = Path(__file__).with_name("schema.sql")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA.read_text(encoding="utf-8"))
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def create_session(self, title: str = "新会话", channel: str = "web") -> str:
        sid, ts = uuid.uuid4().hex[:12], now()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO sessions(id, title, channel, created_at, updated_at)"
                " VALUES (?,?,?,?,?)",
                (sid, title.strip() or "新会话", channel, ts, ts),
            )
        return sid

    def list_sessions(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM sessions ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_session(self, sid: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        return dict(row) if row else None

    def append_message(
        self,
        session_id: str,
        role: str,
        content: str = "",
        tool_calls: list | None = None,
        tool_call_id: str = "",
    ) -> int:
        ts = now()
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO messages(session_id, role, content, tool_calls, tool_call_id,"
                " created_at) VALUES (?,?,?,?,?,?)",
                (
                    session_id,
                    role,
                    content,
                    json.dumps(tool_calls or [], ensure_ascii=False),
                    tool_call_id,
                    ts,
                ),
            )
            self._conn.execute("UPDATE sessions SET updated_at=? WHERE id=?", (ts, session_id))
            return int(cur.lastrowid or 0)

    def messages(self, session_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE session_id=? ORDER BY id", (session_id,)
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["tool_calls"] = json.loads(item["tool_calls"] or "[]")
            out.append(item)
        return out

    def create_run(self, session_id: str) -> str:
        rid = uuid.uuid4().hex[:12]
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO runs(id, session_id, status, started_at) VALUES (?,?,?,?)",
                (rid, session_id, "running", now()),
            )
        return rid

    def finish_run(
        self, run_id: str, status: str, error: str = "", usage: dict | None = None
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE runs SET status=?, error=?, usage=?, ended_at=? WHERE id=?",
                (
                    status,
                    error[:4000],
                    json.dumps(usage or {}, ensure_ascii=False),
                    now(),
                    run_id,
                ),
            )

    def get_run(self, run_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            return None
        item = dict(row)
        item["usage"] = json.loads(item["usage"] or "{}")
        return item

    def stop_orphan_runs(self) -> int:
        """进程重启后，把上次没来得及收尾的 running 标成 stopped。"""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE runs SET status='stopped', error='进程重启', ended_at=?"
                " WHERE status='running'",
                (now(),),
            )
            return int(cur.rowcount)

    def add_event(self, run_id: str, kind: str, data: dict) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO events(run_id, type, data, created_at) VALUES (?,?,?,?)",
                (run_id, kind, json.dumps(data, ensure_ascii=False), now()),
            )
            return int(cur.lastrowid or 0)

    def events(self, run_id: str, since: int = 0) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE run_id=? AND id>? ORDER BY id", (run_id, since)
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["data"] = json.loads(item["data"] or "{}")
            out.append(item)
        return out

    def record_tool_call(
        self, run_id: str, name: str, args: dict, result: str, ok: bool, ms: int
    ) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO tool_calls(run_id, name, args, result, ok, ms, created_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (
                    run_id,
                    name,
                    json.dumps(args, ensure_ascii=False),
                    result[:12000],
                    int(ok),
                    ms,
                    now(),
                ),
            )
            return int(cur.lastrowid or 0)
```

- [ ] **Step 4: 跑，确认通过**

Run: `.venv/bin/python tests/agent_smoke.py`

Expected: `ok   test_store_roundtrip`、`ok   test_store_stops_orphan_runs`。

- [ ] **Step 5: 提交**

```bash
git add apps/device-mac/dongdong/schema.sql apps/device-mac/dongdong/store.py tests/agent_smoke.py
git commit -m "feat: add sqlite store for agent runtime"
```

---

### Task 2: OpenAI 兼容流式客户端

**Files:**
- Create: `apps/device-mac/dongdong/llm.py`
- Test: `tests/agent_smoke.py`

**Interfaces:**
- Produces: `ModelConfig(base_url, api_key, model, temperature, max_tokens)`、`ToolCall(id, name, arguments)`、`StreamEvent(text, tool_calls, usage)`、`parse_data_line(line) -> dict | None`、`merge_tool_calls(acc, deltas)`、`finish_tool_calls(acc) -> list[ToolCall]`、`async stream_chat(cfg, messages, tools) -> AsyncIterator[StreamEvent]`。

- [ ] **Step 1: 写失败测试**

```python
from dongdong import llm
from dongdong.llm import ToolCall


def test_sse_data_line():
    assert llm.parse_data_line('data: {"choices":[]}') == {"choices": []}
    assert llm.parse_data_line("data: [DONE]") is None
    assert llm.parse_data_line(": keep-alive") is None
    assert llm.parse_data_line("data: not-json") is None


def test_tool_call_chunks_are_merged():
    acc: dict = {}
    llm.merge_tool_calls(
        acc,
        [{"index": 0, "id": "c1", "function": {"name": "read_file", "arguments": '{"pa'}}],
    )
    llm.merge_tool_calls(acc, [{"index": 0, "function": {"arguments": 'th":"x"}'}}])
    assert llm.finish_tool_calls(acc) == [
        ToolCall(id="c1", name="read_file", arguments='{"path":"x"}')
    ]


def test_tool_call_without_id_gets_one():
    acc: dict = {}
    llm.merge_tool_calls(acc, [{"index": 2, "function": {"name": "glob", "arguments": "{}"}}])
    calls = llm.finish_tool_calls(acc)
    assert calls[0].id == "call_2"
```

- [ ] **Step 2: 跑，确认失败**

Run: `.venv/bin/python tests/agent_smoke.py`

Expected: `ModuleNotFoundError: No module named 'dongdong.llm'`。

- [ ] **Step 3: 写 llm.py**

```python
# apps/device-mac/dongdong/llm.py
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
```

- [ ] **Step 4: 跑，确认通过**

Run: `.venv/bin/python tests/agent_smoke.py`

Expected: 三个 `ok`。

- [ ] **Step 5: 提交**

```bash
git add apps/device-mac/dongdong/llm.py tests/agent_smoke.py
git commit -m "feat: add openai-compatible streaming client"
```

---

### Task 3: 内置工具（shell + 文件）

**Files:**
- Create: `apps/device-mac/dongdong/builtin_tools.py`
- Test: `tests/agent_smoke.py`

**Interfaces:**
- Produces: `ToolContext(workspace: Path, timeout_s: int)`、`TOOL_SPECS: list[dict]`、`resolve_path(workspace, raw) -> Path`、`async call_tool(name, args, ctx) -> str`。

- [ ] **Step 1: 写失败测试**

```python
from dongdong.builtin_tools import ToolContext, call_tool, resolve_path


def test_path_guard_rejects_escape():
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp).resolve()
        for bad in ("../x", "/etc/passwd"):
            try:
                resolve_path(workspace, bad)
            except ValueError:
                pass
            else:
                raise AssertionError(f"越界路径被放行: {bad}")


def test_file_tools_roundtrip():
    async def run():
        with tempfile.TemporaryDirectory() as tmp:
            ctx = ToolContext(workspace=Path(tmp).resolve())
            await call_tool("write_file", {"path": "a.txt", "content": "hello"}, ctx)
            assert await call_tool("read_file", {"path": "a.txt"}, ctx) == "hello"
            await call_tool("edit_file", {"path": "a.txt", "old": "hello", "new": "world"}, ctx)
            assert (Path(tmp) / "a.txt").read_text() == "world"

    asyncio.run(run())


def test_shell_timeout_and_output():
    async def run():
        with tempfile.TemporaryDirectory() as tmp:
            ctx = ToolContext(workspace=Path(tmp).resolve())
            out = await call_tool("shell", {"command": "pwd"}, ctx)
            assert "exit_code=0" in out
            out = await call_tool("shell", {"command": "sleep 2", "timeout_s": 0.2}, ctx)
            assert "超时" in out
            out = await call_tool("shell", {"command": "yes x | head -c 1000000"}, ctx)
            assert "exit_code=0" in out
            assert len(out) < 13_000, "输出必须被截断"

    asyncio.run(run())
```

- [ ] **Step 2: 跑，确认失败**

Run: `.venv/bin/python tests/agent_smoke.py`

Expected: `ModuleNotFoundError: No module named 'dongdong.builtin_tools'`。

- [ ] **Step 3: 写 builtin_tools.py**

```python
# apps/device-mac/dongdong/builtin_tools.py
"""内置工具：shell、读、写、改、列目录、glob、grep。全部限制在工作区内。"""

from __future__ import annotations

import asyncio
import inspect
import os
import re
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
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
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
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string"},
                },
                "required": ["pattern"],
            },
        },
    },
]


def resolve_path(workspace: Path, raw: str) -> Path:
    if not raw or "\x00" in raw:
        raise ValueError("路径为空或含非法字符")
    path = Path(os.path.expanduser(raw))
    if not path.is_absolute():
        path = workspace / path
    path = path.resolve()
    if path != workspace and not path.is_relative_to(workspace):
        raise ValueError(f"路径越界: {raw}")
    return path


async def _drain(stream, limit: int) -> bytes:
    """边读边丢，超过 limit 只保留前 limit 字节，避免巨量输出打爆内存。"""
    data = bytearray()
    while True:
        chunk = await stream.read(8192)
        if not chunk:
            return bytes(data)
        if len(data) < limit:
            data.extend(chunk[: limit - len(data)])


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
    )
    assert proc.stdout is not None
    reader = asyncio.create_task(_drain(proc.stdout, MAX_OUTPUT))
    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        out = await reader
        text = out.decode("utf-8", "replace")
        return f"命令超时（{timeout:g}s）\n{text[-MAX_OUTPUT:]}"
    except asyncio.CancelledError:
        proc.kill()
        await proc.wait()
        raise
    text = (await reader).decode("utf-8", "replace")
    return f"exit_code={proc.returncode}\n{text[-MAX_OUTPUT:]}"


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
                    rel = path.relative_to(ctx.workspace)
                    hits.append(f"{rel}:{lineno}: {line.strip()[:300]}")
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
```

- [ ] **Step 4: 跑，确认通过**

Run: `.venv/bin/python tests/agent_smoke.py`

Expected: 新增三个 `ok`，其中 shell 超时用 `sleep 2` + `timeout_s=0.2`。

- [ ] **Step 5: 提交**

```bash
git add apps/device-mac/dongdong/builtin_tools.py tests/agent_smoke.py
git commit -m "feat: add builtin coding tools with path guard"
```

---

### Task 4: Agent loop

**Files:**
- Create: `apps/device-mac/dongdong/runtime.py`
- Test: `tests/agent_smoke.py`

**Interfaces:**
- Consumes: `Store`、`ModelConfig`、`StreamEvent`、`ToolCall`、`TOOL_SPECS`、`ToolContext`、`call_tool`。
- Produces: `Runtime(store, model, workspace, *, max_steps=20, history_messages=30, stream_fn=stream_chat)`、`async Runtime.run(session_id, user_text, on_event=None) -> str`。

- [ ] **Step 1: 写失败测试**

```python
from dongdong.llm import ModelConfig, StreamEvent, ToolCall
from dongdong.runtime import Runtime
from dongdong.store import Store


def test_agent_loop_executes_tool_then_answers():
    async def run():
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            store = Store(workspace / "t.db")
            sid = store.create_session("测试")
            calls = []

            async def fake_stream(cfg, messages, tools):
                calls.append(messages)
                if len(calls) == 1:
                    yield StreamEvent(
                        tool_calls=[
                            ToolCall(
                                id="c1",
                                name="write_file",
                                arguments='{"path":"x.txt","content":"hi"}',
                            )
                        ]
                    )
                else:
                    yield StreamEvent(text="写好了")

            runtime = Runtime(
                store,
                ModelConfig("http://unused", "k", "m"),
                workspace,
                stream_fn=fake_stream,
            )
            events = []
            answer = await runtime.run(
                sid, "建个文件", on_event=lambda kind, data: events.append(kind)
            )
            assert answer == "写好了"
            assert (workspace / "x.txt").read_text() == "hi"
            roles = [m["role"] for m in store.messages(sid)]
            assert roles == ["user", "assistant", "tool", "assistant"]
            assert "tool_call" in events and "done" in events
            store.close()

    asyncio.run(run())


def test_history_never_starts_with_orphan_tool():
    async def run():
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "t.db")
            sid = store.create_session("测试")
            store.append_message(
                sid,
                "assistant",
                "",
                tool_calls=[{"id": "c1", "type": "function", "function": {"name": "x"}}],
            )
            store.append_message(sid, "tool", "result", tool_call_id="c1")
            store.append_message(sid, "user", "继续")

            async def fake_stream(cfg, messages, tools):
                assert [m["role"] for m in messages] == ["system", "user", "user"]
                yield StreamEvent(text="ok")

            runtime = Runtime(
                store,
                ModelConfig("http://unused", "k", "m"),
                Path(tmp),
                history_messages=3,
                stream_fn=fake_stream,
            )
            assert await runtime.run(sid, "hi") == "ok"
            store.close()

    asyncio.run(run())
```

- [ ] **Step 2: 跑，确认失败**

Run: `.venv/bin/python tests/agent_smoke.py`

Expected: `ModuleNotFoundError: No module named 'dongdong.runtime'`。

- [ ] **Step 3: 写 runtime.py**

```python
# apps/device-mac/dongdong/runtime.py
"""Agent loop：模型流 → 工具调用 → 工具结果 → 再问模型，直到没有工具调用。"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable

from . import builtin_tools
from .llm import ModelConfig, StreamEvent, ToolCall, stream_chat
from .store import Store

SYSTEM_PROMPT = """你是咚咚，一个跑在用户 Mac 上的通用 agent。
你只能通过工具操作文件系统和 shell，所有路径都限制在工作区内。
工作区：{workspace}

规则：
- 先读再改；不确定就查。
- 修改文件用 edit_file（旧文本必须唯一），新建/覆盖用 write_file。
- 跑命令用 shell，命令要短，不要用交互式命令。
- 不要编造工具结果；工具报错就换方案。
- 回答简洁，说清楚做了什么、结果是什么。"""

Emit = Callable[[str, dict], None]


def _trim(rows: list[dict], limit: int) -> list[dict]:
    rows = rows[-limit:]
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
                out.extend(rows[index:end])
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

    def _messages(self, session_id: str) -> list[dict]:
        rows = _trim(self.store.messages(session_id), self.history_messages)
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

    async def run(
        self, session_id: str, user_text: str, on_event: Emit | None = None
    ) -> str:
        emit: Emit = on_event or (lambda kind, data: None)
        run_id = self.store.create_run(session_id)
        self.store.append_message(session_id, "user", user_text)
        self.store.add_event(run_id, "run_start", {"session_id": session_id})
        emit("run_start", {"run_id": run_id})
        usage: dict = {}
        try:
            for _ in range(self.max_steps):
                messages = self._messages(session_id)
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
            self.store.finish_run(run_id, "stopped", error="用户停止")
            self.store.add_event(run_id, "stopped", {})
            emit("stopped", {})
            raise
        except Exception as exc:
            self.store.finish_run(run_id, "error", error=str(exc))
            self.store.add_event(run_id, "error", {"message": str(exc)})
            emit("error", {"message": str(exc)})
            raise
```

- [ ] **Step 4: 跑，确认通过**

Run: `.venv/bin/python tests/agent_smoke.py`

Expected: 两个新增 `ok`。如果 `test_history_never_starts_with_orphan_tool` 失败，检查 `_trim` 是否把 `assistant(tool_calls)` 和对应 `tool` 一起保留。

- [ ] **Step 5: 提交**

```bash
git add apps/device-mac/dongdong/runtime.py tests/agent_smoke.py
git commit -m "feat: add agent loop with tool execution"
```

---

### Task 5: 配置与终端入口

**Files:**
- Modify: `apps/device-mac/dongdong/config.py`
- Create: `apps/device-mac/dongdong/chat_cli.py`
- Modify: `apps/device-mac/dongdong/__main__.py`
- Test: `tests/agent_smoke.py`

- [ ] **Step 1: 写失败测试**

```python
from dongdong import config


def test_runtime_config_defaults():
    cfg = config.load()
    assert cfg["model"]["model"] == "deepseek-flash"
    assert cfg["runtime"]["max_steps"] == 20
    assert cfg["workspace"]
    os.environ["SMOKE_MODEL_KEY"] = "k123"
    assert config.expand("${SMOKE_MODEL_KEY}") == "k123"
```

- [ ] **Step 2: 跑，确认失败**

Run: `.venv/bin/python tests/agent_smoke.py`

Expected: `KeyError: 'model'`。

- [ ] **Step 3: 改 config、加 chat_cli 和子命令**

`config.py` 的 `DEFAULTS` 加：

```python
    "workspace": "~/OtherProjects",
    "model": {
        "base_url": "https://api.deepseek.com",
        "api_key": "${DEEPSEEK_API_KEY}",
        "model": "deepseek-flash",
        "temperature": 0.2,
        "max_tokens": 4096,
        "extra_body": {"thinking": {"type": "disabled"}},
    },
    "runtime": {"max_steps": 20, "history_messages": 30},
```

`config.py` 顶部加 `import re`，并加一个只做展开的纯函数。`load()` 不展开，避免 `ensure()` 把展开后的 key 写回配置文件：

```python
ENV_REF = re.compile(r"\$\{(\w+)\}")


def expand(value: str) -> str:
    return ENV_REF.sub(lambda m: os.environ.get(m.group(1), ""), value)


def load() -> dict:
    return _merge(DEFAULTS, _read())
```

`ensure()` 里创建 `HOME` 并 `chmod(0o700)`，不创建 `workspace`：

```python
def ensure() -> dict:
    HOME.mkdir(parents=True, exist_ok=True, mode=0o700)
    HOME.chmod(0o700)
    cfg = load()
    if not cfg["device_id"] or not cfg["client_id"]:
        cfg["device_id"] = _mac_like()
        cfg["client_id"] = str(uuid.uuid4())
        save(cfg)
    return cfg
```

`chat_cli.py`：

```python
"""终端里的 agent 对话入口。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from . import config
from .llm import ModelConfig
from .runtime import Runtime
from .store import Store


def _model(cfg: dict) -> ModelConfig:
    model = cfg["model"]
    api_key = config.expand(model["api_key"])
    if not api_key:
        raise SystemExit(
            "没有模型 key：在 ~/.dongdong/config.json 设置 model.api_key 或导出 DEEPSEEK_API_KEY"
        )
    return ModelConfig(
        base_url=model["base_url"],
        api_key=api_key,
        model=model["model"],
        temperature=float(model["temperature"]),
        max_tokens=int(model["max_tokens"]),
    )


def _print_event(kind: str, data: dict) -> None:
    if kind == "text":
        print(data["delta"], end="", flush=True)
    elif kind == "tool_call":
        print(f"\n[工具] {data['name']} {data['arguments']}\n", flush=True)
    elif kind == "tool_result":
        print(f"[结果] {data['result'][:600]}\n", flush=True)
    elif kind == "error":
        print(f"\n[错误] {data['message']}\n", flush=True)
    elif kind == "done":
        print("\n", flush=True)


async def main(cfg: dict, message: str | None = None) -> None:
    store = Store(config.HOME / "dongdong.db")
    store.stop_orphan_runs()
    workspace = Path(cfg["workspace"]).expanduser().resolve()
    if not workspace.is_dir():
        raise SystemExit(f"工作区不存在: {workspace}")
    runtime = Runtime(
        store,
        _model(cfg),
        workspace,
        max_steps=int(cfg["runtime"]["max_steps"]),
        history_messages=int(cfg["runtime"]["history_messages"]),
    )
    sid = store.create_session("终端会话", "cli")
    if message:
        await runtime.run(sid, message, on_event=_print_event)
        store.close()
        return

    print("咚咚 agent。输入 /new 新会话，/sessions 看会话，/quit 退出。")
    while True:
        try:
            text = (await asyncio.to_thread(input, "你> ")).strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        if text == "/quit":
            break
        if text == "/new":
            sid = store.create_session("终端会话", "cli")
            print(f"新会话 {sid}")
            continue
        if text == "/sessions":
            for item in store.list_sessions()[:10]:
                print(f"{item['id']}  {item['channel']}  {item['title']}")
            continue
        task = asyncio.create_task(runtime.run(sid, text, on_event=_print_event))
        try:
            await task
        except asyncio.CancelledError:
            print("\n已停止")
    store.close()
```

`__main__.py` 加子命令：

```python
    chat = sub.add_parser("chat", help="终端里和内置 agent 对话")
    chat.add_argument("--message", help="只跑一句")
```

在 `args.cmd == "serve"` 之前加：

```python
    if args.cmd == "chat":
        from . import chat_cli

        asyncio.run(chat_cli.main(cfg, args.message))
        return
```

- [ ] **Step 4: 跑，确认通过**

Run: `.venv/bin/python tests/agent_smoke.py`

Expected: 全部 `ok`。

手工验证（需要 key）：

```bash
export DEEPSEEK_API_KEY=sk-...
./scripts/dev.sh chat --message "列出工作区里的文件，然后告诉我哪个是 README"
```

Expected: 打印工具调用和最终回答；`~/.dongdong/dongdong.db` 里能看到会话。

- [ ] **Step 5: 提交**

```bash
git add apps/device-mac/dongdong/config.py apps/device-mac/dongdong/chat_cli.py apps/device-mac/dongdong/__main__.py tests/agent_smoke.py
git commit -m "feat: add chat command for builtin agent"
```

---

### Task 6: 测试入口、文档、收尾

**Files:**
- Modify: `scripts/check.sh`
- Modify: `docs/decisions.md`
- Modify: `docs/architecture.md`
- Modify: `docs/agents.md`
- Modify: `tests/agent_smoke.py`

- [ ] **Step 1: 加测试运行器**

Task 1 已经提前补了这个运行器（否则 `python tests/agent_smoke.py` 会静默跑 0 个测试）。先确认文件末尾已有该块；没有才补，不要重复加。顺带在运行器结束时清理 `DONGDONG_HOME` 临时目录（`shutil.rmtree(..., ignore_errors=True)`）。

`tests/agent_smoke.py` 末尾应有：

```python
if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            result = fn()
            if asyncio.iscoroutine(result):
                asyncio.run(result)
            print(f"ok   {name}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL {name}: {exc}")
    print("agent smoke 通过" if not failures else f"{failures} 个失败")
    sys.exit(1 if failures else 0)
```

`scripts/check.sh` 加一行：

```bash
.venv/bin/python tests/agent_smoke.py
```

- [ ] **Step 2: 跑全量检查**

Run: `./scripts/check.sh`

Expected: ruff 通过；`smoke 通过`；`agent smoke 通过`。

- [ ] **Step 3: 写决策记录**

`docs/decisions.md` 追加：

| # | 决策 | 理由 | 代价 |
| --- | --- | --- | --- |
| 10 | 新 agent runtime 用 SQLite，存 `~/.dongdong/dongdong.db` | 本地单用户，标准库够用，不引入 Postgres/ORM | 多进程/多用户要换 |
| 11 | 模型协议只做 OpenAI 兼容 | 灵矽自定义模型本身就是 OpenAI 兼容；DeepSeek/OpenAI 都通 | Anthropic 原生协议以后再加 |
| 12 | 新 runtime 与旧 `agent.py` 并存 | 不打断现有灵矽语音链路，迁移可以分阶段 | 两套入口短期共存 |
| 13 | 测试拆到 `tests/agent_smoke.py` + `tests/agent_transport.py` | agent loop 边界和 HTTP/SSE 传输必须自动测；`smoke.py` 已接近 300 行 | 偏离「唯一测试文件」，由 `check.sh` 统一跑 |

- [ ] **Step 4: 更新架构文档**

`docs/architecture.md` 加一小节“内置 agent（新）”，说明：

- `runtime.py` 是 agent loop，`llm.py` 是模型客户端，`builtin_tools.py` 是工具，`store.py` 是 SQLite。
- 旧 `agent.py` 仍是“CLI 执行器”，灵矽链路暂时不动。
- Web/语音后续复用 `Runtime`。

`docs/agents.md` 顶部加一句“本文只描述旧的 CLI 执行器；新内置 agent 见 `runtime.py` / 本计划”，避免后来的人以为还要往 `agents` 配置里加东西。

- [ ] **Step 5: 提交**

```bash
git add scripts/check.sh docs/decisions.md docs/architecture.md docs/agents.md tests/agent_smoke.py
git commit -m "chore: wire agent smoke tests and document runtime"
```

---

## Self-Review

- **Spec coverage:** 这个计划覆盖 spec 第 4 节（agent runtime）和第 6 节（SQLite 数据）。Web、Skills、MCP、灵矽自定义模型端点不在这个计划里，分别下一份计划。
- **Placeholder scan:** 没有 TBD；每个任务都有测试、实现和验证命令。
- **Type consistency:** `Store`、`ModelConfig`、`ToolCall`、`StreamEvent`、`ToolContext`、`Runtime.run` 的签名在任务间一致；`run` 返回 `str`，事件是 `(kind, dict)`。
- **Review Focus:** 五个风险都有对应测试：Task 3 路径/超时，Task 2 参数拆包，Task 4 历史裁剪/取消，Task 1 的 `stop_orphan_runs` 负责重启后的 `running` 收尾（Web 启动时调用即可）。

## Execution Handoff

计划已保存到 `docs/superpowers/plans/2026-10-24-agent-runtime.md`。请先审阅；确认后选择执行方式：

- **Subagent-driven**：每个任务由独立 subagent 实现，再由独立 reviewer 检查；适合这种接口多、容易写歪的 runtime。
- **Native**：我在当前会话里按任务顺序实现，最后统一 review；更快，但中途没有独立审查。

我建议 **Subagent-driven**，因为 `store/llm/tools/runtime` 的接口是环环相扣的，早一点让 reviewer 卡住类型和边界，比最后一起修便宜。
