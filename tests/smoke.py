from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "device-mac"))
os.environ["DONGDONG_HOME"] = tempfile.mkdtemp(prefix="dongdong-smoke-")
logging.disable(logging.CRITICAL)  # 预期内的报错别刷屏

from dongdong import agent, config, mcp, memory, screen, tools, work_gate  # noqa: E402
from dongdong.linx import _hello  # noqa: E402


def reject(fn, *args, error=ValueError):
    with contextlib.suppress(error):
        fn(*args)
        raise AssertionError("边界输入必须拒绝")


def test_linx_mcp_and_plan_protocols():
    hello, params = _hello(), _hello()["audio_params"]
    assert hello["features"]["mcp"] is True
    assert {"format": "opus", "sample_rate": 16000, "frame_duration": 60}.items() <= params.items()
    from dongdong.runtime import _plan

    step = {"step": " 检查 ", "status": "in_progress"}
    assert _plan({"steps": [step]})["steps"][0]["step"] == "检查"
    for steps in ([], ["bad"], [{**step, "status": "done"}], [step, step]):
        reject(_plan, {"steps": steps})
    assert tools.TOOLS, "一个工具都没有"
    for name, tool in tools.TOOLS.items():
        assert name.startswith("self."), name
        described = tool.describe()
        schema = described["inputSchema"]
        assert schema["type"] == "object" and described["type"] == 0
        assert set(schema["required"]) <= set(schema["properties"]), name
    assert mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    init = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert init["result"]["protocolVersion"] == "2024-11-05"
    listed = mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert {t["name"] for t in listed["result"]["tools"]} == set(tools.TOOLS)
    assert {"self.memory." + op for op in ("search", "save", "list", "delete")} <= set(tools.TOOLS)
    request = {"jsonrpc": "2.0", "id": 3, "method": "tools/call"}
    assert mcp.handle({**request, "params": {"name": "self.nope"}})["error"]["code"] == -32601
    bad = {"name": "self.code.run", "arguments": {"instruction": "  "}}
    assert mcp.handle({**request, "params": bad})["result"]["isError"] is True


def test_agent_path_argv_and_env_boundaries():
    work = Path(tempfile.mkdtemp(prefix="dongdong-work-")).resolve()  # macOS 上 /var → /private/var
    cfg = {"projects": [str(work)], "cwd": ""}
    assert agent.resolve_dir(cfg, None) == work
    (work / "proj").mkdir()
    assert agent.resolve_dir(cfg, "proj") == work / "proj"
    for outside in ("/tmp", "/etc", str(work / ".."), "nope", "../etc"):
        reject(agent.resolve_dir, cfg, outside)
    assert not agent.required_env({"cmd": ["x"]})
    assert "DEEPSEEK_API_KEY" in agent.required_env(config.load()["agents"]["deepseek"])
    os.environ["SMOKE_KEY"] = "k123"
    assert agent._expand({"K": "${SMOKE_KEY}"}) == {"K": "k123"}
    assert agent._expand({"K": "${NOT_SET_AT_ALL}"}) == {"K": ""}
    with tempfile.TemporaryDirectory() as tmp:
        cmd = [sys.executable, "-c", "import os;os.write(1,bytes([255]))"]
        job = agent.Job("fake", Path(tmp), cmd, dict(os.environ))
        assert job.done.wait(2) and not work_gate.current() and "\ufffd" in job.output


def test_memory_delete_validates_all_owners_before_deleting():
    records = {"owned": {"user_id": memory.namespace("u", "a")}, "other": {"user_id": "u"}}
    deleted = []
    stub = SimpleNamespace(get=lambda memory_id: records.get(memory_id))
    stub.delete = lambda memory_id: deleted.append(memory_id)
    with patch.object(memory, "_memory", stub):
        reject(memory.delete, ["owned", "other"], memory.namespace("u", "a"))
        assert deleted == [], "必须先验证完整列表再删除"
        memory.delete(["owned"], memory.namespace("u", "a"))
        assert deleted == ["owned"]
    initialized = []
    with patch.object(memory, "_get_memory", lambda: initialized.append(True)):
        for name, args in (
            ("delete", {"memory_ids": []}),
            ("search", {"query": ""}),
            ("save", {"text": "   "}),
        ):
            assert tools.call("self.memory." + name, args)["isError"] is True
        assert not initialized, "空输入不应初始化 Mem0 SDK"
    with patch.object(memory, "save", lambda text, user_id: {"results": []}):
        assert tools._memory_save({"text": "长期偏好"}) == "没有提取到可保存的新记忆。"
    module = ModuleType("mem0")
    module.Memory = SimpleNamespace(from_config=lambda config: config)
    with tempfile.TemporaryDirectory(prefix="dongdong-memory-") as temp_dir:
        data_dir = Path(temp_dir) / ".memory"
        data_dir.mkdir()
        data_dir.chmod(0o755)
        with (
            patch.object(memory, "DATA_DIR", data_dir),
            patch.object(memory, "_memory", None),
            patch.dict(os.environ, OPENAI_API_KEY="test-only"),
            patch.dict(sys.modules, mem0=module),
        ):
            memory._get_memory()
            assert data_dir.stat().st_mode & 0o777 == 0o700


def test_opus_roundtrip_and_airplay_escape():
    import opuslib

    enc = opuslib.Encoder(16000, 1, opuslib.APPLICATION_VOIP)
    dec = opuslib.Decoder(16000, 1)
    packet = enc.encode(b"\x00\x01" * 960, 960)
    assert 0 < len(packet) < 200 and len(dec.decode(packet, 5760)) == 1920
    script = screen._mirror_script('卧室"电视')
    assert '\\"电视' in script
    assert "{TARGET}" not in script and "{MODE}" not in script


def test_web_access_project_cwd_and_reentry_boundaries():
    import httpx
    from dongdong.web_server import _local_host, create_app

    async def check():
        app = create_app(config.load())
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
            ) as client:
                cases = [
                    ("GET", "/api/state", {}, 200),
                    ("GET", "/api/settings", {"headers": {"Host": "evil.example"}}, 400),
                    ("POST", "/api/sessions", {"headers": {"Origin": "https://evil.example"}}, 403),
                    ("PUT", "/api/settings", {"json": {"max_tokens": -1}}, 422),
                ]
                for method, url, kwargs, status in cases:
                    assert (await client.request(method, url, **kwargs)).status_code == status
                assert "api_key" not in (await client.get("/api/settings")).json()
        reject(_local_host, "0.0.0.0")

    asyncio.run(check())
    from dongdong.web_runtime import WebState

    async def check():
        cfg = config.load()
        cfg["workspace"], cfg["model"]["api_key"] = str(config.HOME), "boundary-only"
        state = WebState(cfg)
        a, b = config.HOME / "a", config.HOME / "b"
        a.mkdir()
        b.mkdir()
        (a / "escape").symlink_to(b, target_is_directory=True)
        pid = state.store.create_project("a", str(a))
        sid = state.store.create_session(project_id=pid)
        assert work_gate.acquire("settings")
        try:
            for cwd, error in [(str(b), ValueError), ("escape", ValueError), (None, RuntimeError)]:
                with contextlib.suppress(error):
                    await state.start(sid, "boundary", cwd)
                    raise AssertionError("越界或重入必须拒绝")
                assert not state.store.list_runs(sid)
        finally:
            work_gate.release("settings")
            await state.close()
        from dongdong.audio import Player, Recorder

        recorder, player = Recorder(), Player()
        recorder.queue.put(b"old mic")
        player.queue.put(b"old speech")
        player.buffer.extend(b"old")
        recorder.close()
        player.close()
        assert recorder.read() is None and not player.playing()

    asyncio.run(check())


def test_skill_approval_and_mcp_cleanup_boundaries():
    from dongdong import skills
    from dongdong.approvals import Approvals

    root = config.HOME / "skills"
    (root / "sample").mkdir(parents=True)
    (root / "sample" / "SKILL.md").write_text("技能正文")
    (root / "escape").symlink_to(root.parent, target_is_directory=True)
    assert skills.read_skill("sample", {}) == "技能正文"
    for name, cfg in [
        ("../sample", {}),
        ("escape", {}),
        ("sample", {"disabled_skills": ["sample"]}),
    ]:
        reject(skills.read_skill, name, cfg)

    async def check():
        manager, events = Approvals(), []
        for approved in (False, True):
            request = manager.request("run", "call", "shell", {}, lambda k, d: events.append(k))
            task = asyncio.create_task(request)
            await asyncio.sleep(0)
            assert not manager.resolve("other", "call", True)
            assert manager.resolve("run", "call", approved) and not manager.resolve(
                "run", "call", approved
            )
            assert await task is approved and not manager.pending()
        request = manager.request("run", "cancel", "shell", {}, lambda k, d: events.append(k))
        task = asyncio.create_task(request)
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert not manager.pending() and events.count("approval_result") == 3

    asyncio.run(check())
    from dongdong.mcp_client import MCPTools

    async def broken_cleanup():
        raise ExceptionGroup("cleanup", [RuntimeError("secret")])

    async def broken_connect(stack, server):
        stack.push_async_callback(broken_cleanup)
        raise ValueError("secret")

    async def check():
        async with MCPTools({}) as external:
            external._stack.push_async_callback(broken_cleanup)
        assert external.errors == ["连接清理失败 (ExceptionGroup)"]
        try:
            async with MCPTools({}) as external:
                external._stack.push_async_callback(broken_cleanup)
                raise asyncio.CancelledError("body")
        except asyncio.CancelledError as error:
            assert str(error) == "body"
        else:
            raise AssertionError("清理异常不能覆盖取消")
        external = MCPTools({"bad": {"enabled": True}})
        with patch.object(external, "_connect", broken_connect):
            async with external:
                assert external.errors == ["bad: 连接或发现失败 (ValueError)"]

    asyncio.run(check())


def test_project_migration_archive_preserves_records():
    from dongdong.store import Store

    path = config.HOME / "migration.db"
    store = Store(path)
    sid = store.create_session("旧会话")
    store.append_message(sid, "user", "保留正文")
    rid = store.create_run(sid)
    store.add_event(rid, "text", {"delta": "保留结果"})
    store.record_tool_call(rid, "shell", {}, "保留工具结果", True, 1)
    store.finish_run(rid, "done")
    store.close()
    with sqlite3.connect(path) as db:
        db.executescript(
            "DROP INDEX sessions_project_archive; ALTER TABLE sessions DROP COLUMN project_id;"
            "ALTER TABLE sessions DROP COLUMN archived_at; DROP TABLE projects;"
        )
        before = {
            t: db.execute(f"SELECT * FROM {t}").fetchall()
            for t in ("sessions", "messages", "runs", "events", "tool_calls")
        }
    for _ in range(2):
        store = Store(path)
        assert store.get_session(sid)["project_id"] is None
        for table, rows in before.items():
            actual = [tuple(r) for r in store._conn.execute(f"SELECT * FROM {table}")]
            assert [r[: len(rows[0])] for r in actual] == rows
        store.close()
    store = Store(path)
    pid = store.create_project("项目", str(config.HOME))
    updated = store.get_session(sid)["updated_at"]
    store.update_session(sid, {"archived_at": "2026-10-25", "project_id": pid})
    reject(store.create_run, sid)
    store.update_session(sid, {"archived_at": None, "project_id": None})
    assert store.get_session(sid)["updated_at"] == updated
    assert len(store.messages(sid)) == len(store.events(rid)) == len(store.list_runs(sid)) == 1
    active = store.create_run(sid)
    reject(store.update_session, sid, {"archived_at": "today"})
    store.finish_run(active, "done")
    store.close()


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except Exception as exc:  # noqa: BLE001 测试跑挂了要打印出来
                failures += 1
                print(f"FAIL {name}: {exc}")
    print("smoke 通过" if not failures else f"{failures} 个失败")
    sys.exit(1 if failures else 0)
