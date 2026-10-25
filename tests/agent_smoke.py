import asyncio
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "device-mac"))
SMOKE_HOME = tempfile.mkdtemp(prefix="dongdong-agent-smoke-")
os.environ["DONGDONG_HOME"] = SMOKE_HOME

from dongdong import config, llm  # noqa: E402
from dongdong.builtin_tools import ToolContext, call_tool, resolve_path  # noqa: E402
from dongdong.llm import ModelConfig, StreamEvent, ToolCall  # noqa: E402
from dongdong.runtime import Runtime  # noqa: E402
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
        newer = store.create_session("更新的会话")
        assert store.list_sessions()[0]["id"] == newer
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
        with tempfile.TemporaryDirectory() as outside:
            (Path(outside) / "secret.txt").write_text("x", encoding="utf-8")
            os.symlink(Path(outside) / "secret.txt", workspace / "link")
            try:
                resolve_path(workspace, "link")
            except ValueError:
                pass
            else:
                raise AssertionError("符号链接越界被放行")


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
            started = time.monotonic()
            out = await call_tool("shell", {"command": "yes x | grep zzz", "timeout_s": 0.2}, ctx)
            assert "超时" in out
            assert time.monotonic() - started < 2.0, "超时必须杀掉整个进程组"
            started = time.monotonic()
            out = await call_tool("shell", {"command": "sleep 5 &", "timeout_s": 0.2}, ctx)
            assert "后台" in out
            assert time.monotonic() - started < 2.0, "后台子进程不能无限拖住返回"
            out = await call_tool("shell", {"command": "yes x | head -c 1000000"}, ctx)
            assert "exit_code=0" in out
            assert out.removeprefix("exit_code=0\n") == "x\n" * 500_000, "输出不得被截断"

    asyncio.run(run())


def test_grep_skips_symlink_outside_workspace():
    marker = "dongdong-leak-9f3a"

    async def run():
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            with tempfile.TemporaryDirectory() as outside:
                target = Path(outside) / "secret.txt"
                target.write_text(marker, encoding="utf-8")
                os.symlink(target, workspace / "leak.txt")
                ctx = ToolContext(workspace=workspace)
                out = await call_tool("grep", {"pattern": marker}, ctx)
                assert out == "(无匹配)", f"grep 穿越符号链接泄露了工作区外内容: {out}"

    asyncio.run(run())


def test_agent_loop_executes_tool_then_answers():
    async def run():
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            store = Store(workspace / "t.db")
            sid = store.create_session("测试")
            calls = []
            contents = "完整工具结果\n" * 3000
            (workspace / "large.txt").write_text(contents)

            async def fake_stream(cfg, messages, tools):
                calls.append(messages)
                if len(calls) == 1:
                    yield StreamEvent(
                        tool_calls=[
                            ToolCall(
                                id="c1",
                                name="read_file",
                                arguments='{"path":"large.txt"}',
                            )
                        ]
                    )
                else:
                    assert messages[-1]["content"] == contents
                    yield StreamEvent(text="写好了")

            runtime = Runtime(
                store, ModelConfig("http://unused", "k", "m"), workspace, stream_fn=fake_stream
            )
            events = []
            answer = await runtime.run(
                sid, "建个文件", on_event=lambda kind, data: events.append(kind)
            )
            assert answer == "写好了"
            roles = [m["role"] for m in store.messages(sid)]
            assert roles == ["user", "assistant", "tool", "assistant"]
            with store._lock:
                assert (
                    store._conn.execute("SELECT result FROM tool_calls").fetchone()[0] == contents
                )
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


def test_history_keeps_current_user_request():
    async def run():
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            store = Store(workspace / "t.db")
            sid = store.create_session("测试")
            seen: list[list[dict]] = []

            async def fake_stream(cfg, messages, tools):
                seen.append(messages)
                if len(seen) <= 15:
                    call = ToolCall(id=f"c{len(seen)}", name="list_dir", arguments='{"path":"."}')
                    yield StreamEvent(tool_calls=[call])
                else:
                    yield StreamEvent(text="done")

            runtime = Runtime(
                store,
                ModelConfig("http://unused", "k", "m"),
                workspace,
                max_steps=20,
                history_messages=30,
                stream_fn=fake_stream,
            )
            assert await runtime.run(sid, "long task") == "done"
            assert len(seen) == 16
            assert all(
                any(m["role"] == "user" and m["content"] == "long task" for m in req)
                for req in seen
            )
            store.close()

    asyncio.run(run())


def test_runtime_config_defaults():
    cfg = config.load()
    assert cfg["model"]["base_url"] == "https://api.deepseek.com"
    assert cfg["model"]["model"] == "deepseek-flash"
    assert cfg["model"]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert cfg["runtime"]["max_steps"] == 1000
    assert cfg["workspace"]
    assert cfg["model"]["api_key"] == "${DEEPSEEK_API_KEY}"
    os.environ["SMOKE_MODEL_KEY"] = "k123"
    try:
        assert config.expand("${SMOKE_MODEL_KEY}") == "k123"
    finally:
        os.environ.pop("SMOKE_MODEL_KEY", None)


if __name__ == "__main__":
    failures = 0
    try:
        for name, fn in sorted(globals().items()):
            if name.startswith("test_") and callable(fn):
                try:
                    result = fn()
                    if asyncio.iscoroutine(result):
                        asyncio.run(result)
                    print(f"ok   {name}")
                except Exception as exc:  # noqa: BLE001 测试跑挂了要打印出来
                    failures += 1
                    print(f"FAIL {name}: {exc}")
    finally:
        shutil.rmtree(SMOKE_HOME, ignore_errors=True)
    print("agent smoke 通过" if not failures else f"{failures} 个失败")
    sys.exit(1 if failures else 0)
