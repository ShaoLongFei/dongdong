from __future__ import annotations

import asyncio
import contextlib
import logging
import os
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
from dongdong.store import Store  # noqa: E402
from dongdong.task_model import ItemSpec, RunSpec  # noqa: E402


def test_hello_declares_mcp_and_opus():
    hello = _hello()
    assert hello["features"]["mcp"] is True
    params = hello["audio_params"]
    assert {"format": "opus", "sample_rate": 16000, "frame_duration": 60}.items() <= params.items()


def test_plan_protocol():
    from dongdong.runtime import _plan

    step = {"step": " 检查 ", "status": "in_progress"}
    assert _plan({"steps": [step]})["steps"][0]["step"] == "检查"
    for steps in ([], ["bad"], [{**step, "status": "done"}], [step, step]):
        with contextlib.suppress(ValueError):
            _plan({"steps": steps})
            raise AssertionError("无效计划必须拒绝")


def test_tool_registry_shape():
    assert tools.TOOLS, "一个工具都没有"
    for name, tool in tools.TOOLS.items():
        assert name.startswith("self."), name
        described = tool.describe()
        schema = described["inputSchema"]
        assert schema["type"] == "object" and described["type"] == 0
        assert set(schema["required"]) <= set(schema["properties"]), name


def test_mcp_dispatch():
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


def test_resolve_dir_blocks_escape():
    work = Path(tempfile.mkdtemp(prefix="dongdong-work-")).resolve()  # macOS 上 /var → /private/var
    cfg = {"projects": [str(work)], "cwd": ""}
    assert agent.resolve_dir(cfg, None) == work
    (work / "proj").mkdir()
    assert agent.resolve_dir(cfg, "proj") == work / "proj"
    for outside in ("/tmp", "/etc", str(work / ".."), "nope", "../etc"):
        with contextlib.suppress(ValueError):
            agent.resolve_dir(cfg, outside)
            raise AssertionError(f"越界或不存在的目录被放行: {outside}")


def test_agent_argv_and_env():
    assert not agent.required_env({"cmd": ["x"]})
    assert "DEEPSEEK_API_KEY" in agent.required_env(config.load()["agents"]["deepseek"])
    os.environ["SMOKE_KEY"] = "k123"
    assert agent._expand({"K": "${SMOKE_KEY}"}) == {"K": "k123"}
    assert agent._expand({"K": "${NOT_SET_AT_ALL}"}) == {"K": ""}
    with tempfile.TemporaryDirectory() as tmp:
        job = agent.Job(
            "fake",
            Path(tmp),
            [sys.executable, "-c", "import os;os.write(1,bytes([255]))"],
            dict(os.environ),
        )
        assert job.done.wait(2) and not work_gate.current() and "\ufffd" in job.output


def test_memory_delete_validates_all_owners_before_deleting():
    class MemoryStub:
        records, deleted = {"owned": {"user_id": "u1"}, "other": {"user_id": "u2"}}, []

        def get(self, memory_id):
            return self.records.get(memory_id)

        def delete(self, memory_id):
            self.deleted.append(memory_id)

    stub = MemoryStub()
    with patch.object(memory, "_memory", stub):
        with contextlib.suppress(ValueError):
            memory.delete(["owned", "other"], "u1")
            raise AssertionError("另一用户的 memory ID 必须拒绝")
        assert stub.deleted == [], "必须先验证完整列表再删除"
        assert memory.delete(["owned"], "u1") == ["owned"] and stub.deleted == ["owned"]


def test_memory_tools_reject_empty_inputs_before_sdk_init():
    initialized = []
    with patch.object(memory, "_get_memory", lambda: initialized.append(True)):
        for name, args in (
            ("delete", {"memory_ids": []}),
            ("search", {"query": ""}),
            ("save", {"text": "   "}),
        ):
            assert tools.call("self.memory." + name, args)["isError"] is True
        assert not initialized, "空输入不应初始化 Mem0 SDK"


def test_memory_save_reports_empty_results_accurately():
    with patch.object(memory, "save", lambda text, user_id: {"results": []}):
        response = tools._memory_save({"text": "长期偏好"})
        assert response == "没有提取到可保存的新记忆。", response


def test_memory_tightens_existing_data_dir_permissions():
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


def test_opus_roundtrip():
    import opuslib

    enc = opuslib.Encoder(16000, 1, opuslib.APPLICATION_VOIP)
    dec = opuslib.Decoder(16000, 1)
    packet = enc.encode(b"\x00\x01" * 960, 960)
    assert 0 < len(packet) < 200 and len(dec.decode(packet, 5760)) == 1920


def test_airplay_script_escapes_target():
    script = screen._mirror_script('卧室"电视')
    assert '\\"电视' in script
    assert "{TARGET}" not in script and "{MODE}" not in script


def test_web_local_access_boundary():
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
        with contextlib.suppress(ValueError):
            _local_host("0.0.0.0")
            raise AssertionError("没有鉴权时不能监听公网")

    asyncio.run(check())


def test_shared_runtime_rejects_reentry_and_cwd_escape():
    from dongdong.web_runtime import WebState

    async def check():
        cfg = config.load()
        cfg["workspace"], cfg["model"]["api_key"] = str(config.HOME), "boundary-only"
        state = WebState(cfg)
        sid = state.store.create_session()
        assert work_gate.acquire("settings")
        try:
            for cwd, error in [("/", ValueError), (None, RuntimeError)]:
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


def test_skill_and_approval_boundaries():
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
        with contextlib.suppress(ValueError):
            skills.read_skill(name, cfg)
            raise AssertionError("禁用/越界技能不能读取")

    async def check():
        manager, events = Approvals(), []
        for approved in (False, True):
            task = asyncio.create_task(
                manager.request("run", "call", "shell", {}, lambda k, d: events.append(k))
            )
            await asyncio.sleep(0)
            assert not manager.resolve("other", "call", True)
            assert manager.resolve("run", "call", approved) and not manager.resolve(
                "run", "call", approved
            )
            assert await task is approved and not manager.pending()
        task = asyncio.create_task(
            manager.request("run", "cancel", "shell", {}, lambda k, d: events.append(k))
        )
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert not manager.pending() and events.count("approval_result") == 3

    asyncio.run(check())


def test_mcp_cleanup_preserves_result_and_cancellation():
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


def test_task_store_claims_dag_atomically_and_recovers_lease():
    with tempfile.TemporaryDirectory(prefix="dongdong-task-") as tmp:
        path = Path(tmp) / "tasks.db"
        first = Store(path)
        sid = first.create_session("task")
        run_id = first.create_task_run(sid, RunSpec(goal_text="完成 DAG"))
        first.add_items(
            run_id,
            [
                ItemSpec(id="a", instruction="a"),
                ItemSpec(id="b", instruction="b", depends_on=("a",)),
            ],
        )
        second = Store(path)
        assert first.claim_item(run_id, "a", "one", lease_seconds=1)
        assert not second.claim_item(run_id, "a", "two", lease_seconds=1)
        first.requeue_expired(run_id, now_value="2999-01-01T00:00:00+00:00")
        assert second.claim_item(run_id, "a", "two", lease_seconds=1)
        second.complete_item("a", "two", "ok")
        assert second.claim_item(run_id, "b", "two", lease_seconds=1)
        second.fail_item("b", "two", "blocked", error_kind="blocked")
        view = second.task_view(run_id)
        assert view["items"][0]["status"] == "done"
        assert view["items"][1]["status"] == "blocked"
        with contextlib.suppress(ValueError):
            first.add_items(run_id, [ItemSpec(id="c", instruction="c", depends_on=("missing",))])
            raise AssertionError("缺失依赖必须拒绝")
        with contextlib.suppress(ValueError):
            first.add_items(
                run_id,
                [
                    ItemSpec(id="c", instruction="c", depends_on=("d",)),
                    ItemSpec(id="d", instruction="d", depends_on=("c",)),
                ],
            )
            raise AssertionError("依赖环必须拒绝")
        first.finish_run(run_id, "done")
        assert not second.claim_item(run_id, "b", "late", lease_seconds=1)
        first.close()
        second.close()


def test_meeting_confirmation_and_control_boundaries():
    from dongdong import birdview, skills
    from dongdong.meeting_store import Meetings

    assert birdview.control("咚咚，整理一下方案。") == "generate"
    assert birdview.control("按这个方案实施！") == "confirm"
    assert not any(
        birdview.control(t)
        for t in [
            "不要按这个方案实施",
            "如果说按这个方案实施",
            "“按这个方案实施”",
            "按这个方案实施吗",
            "按这个方案实施？",
        ]
    )
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "meeting.db")
        db, sid = Meetings(store), store.create_session()
        db.open(sid, tmp)
        db.discuss(sid, "修改需求", "voice")
        proposal = db.publish(sid, 1, {"solution": "真实方案"}, "a" * 32)
        db.update(sid, phase="ready")

        def rejected():
            try:
                db.claim(sid, proposal["id"], proposal["hash"], {})
            except ValueError:
                return
            raise AssertionError("未展示/过期/重复方案必须拒绝")

        rejected()
        db.shown(sid, proposal["id"], proposal["hash"])
        db.discuss(sid, "新增意见", "text")
        rejected()
        proposal = db.publish(sid, 2, {"solution": "新版"}, "b" * 32)
        db.shown(sid, proposal["id"], proposal["hash"])
        assert (
            db.claim(sid, proposal["id"], proposal["hash"], {"source": "voice"})["status"]
            == "claimed"
        )
        rejected()
        db.update(sid, phase="executing")
        Meetings(store)
        assert db.get(sid)["phase"] == "interrupted" and not db.proposal(proposal["id"])["shown"]
        store.close()
    root = config.HOME / "skills" / "resource"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text("资源")
    (root / "escape.md").symlink_to(ROOT / "README.md")
    for path in ("../other", "escape.md"):
        with contextlib.suppress(ValueError):
            skills.read_resource("resource", path, {})
            raise AssertionError("技能资源不得越界")


def test_meeting_runtime_rejects_unlisted_tools_and_scope_expansion():
    from dongdong.llm import ModelConfig, StreamEvent, ToolCall
    from dongdong.meeting_tools import MeetingTools
    from dongdong.runtime import Runtime
    from dongdong.runtime_context import ScopeChange

    async def check():
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            store = Store(work / "runtime.db")
            sid = store.create_session()
            called = []

            async def stream(*args):
                if not called:
                    called.append(True)
                    yield StreamEvent(
                        tool_calls=[ToolCall("x", "write_file", '{"path":"bad","content":"bad"}')]
                    )
                else:
                    yield StreamEvent(text="拒绝")

            await Runtime(
                store, ModelConfig("", "", ""), work, cfg={}, toolset=[], stream_fn=stream
            ).run(sid, "只读")
            assert not (work / "bad").exists()
            assert any(
                "不允许" in e["data"].get("result", "")
                for e in store.events(store.list_runs(sid)[0]["id"])
            )
            adapter = MeetingTools(work, {}, None, payload={"files": ["ok"]})
            try:
                await adapter.call("write_file", {"path": "bad"}, lambda *a: None)
            except ScopeChange:
                pass
            else:
                raise AssertionError("范围外写入必须停止")
            store.close()

    asyncio.run(check())


def test_meeting_baseline_detects_uncommitted_and_new_files():
    from dongdong.birdview import fingerprint, git

    async def check():
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp).resolve()
            await git(work, "init", "-q")
            (work / "main.py").write_text("before")
            await git(work, "add", ".")
            await git(
                work,
                "-c",
                "user.name=Smoke",
                "-c",
                "user.email=smoke@example.invalid",
                "commit",
                "-qm",
                "base",
            )
            initial = await fingerprint(work)
            (work / "main.py").write_text("after")
            assert await fingerprint(work) != initial
            (work / "main.py").write_text("before")
            (work / "new.py").write_text("new")
            assert await fingerprint(work) != initial

    asyncio.run(check())


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
