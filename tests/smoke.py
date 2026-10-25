from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
import tempfile
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "device-mac"))
os.environ["DONGDONG_HOME"] = tempfile.mkdtemp(prefix="dongdong-smoke-")
logging.disable(logging.CRITICAL)  # 预期内的报错别刷屏

from dongdong import agent, config, mcp, memory, screen, tools, work_gate  # noqa: E402
from dongdong.linx import _hello  # noqa: E402


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
        assert described["inputSchema"]["type"] == "object"
        assert described["type"] == 0
        schema = described["inputSchema"]
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
    assert agent.required_env({"cmd": ["x"]}) == []
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
        assert job.done.wait(2)
        assert not work_gate.current()
        assert "\ufffd" in job.output


def test_memory_delete_validates_all_owners_before_deleting():
    class MemoryStub:
        records, deleted = {"owned": {"user_id": "u1"}, "other": {"user_id": "u2"}}, []

        def get(self, memory_id):
            return self.records.get(memory_id)

        def delete(self, memory_id):
            self.deleted.append(memory_id)

    original, stub = memory._memory, MemoryStub()
    memory._memory = stub
    try:
        with contextlib.suppress(ValueError):
            memory.delete(["owned", "other"], "u1")
            raise AssertionError("另一用户的 memory ID 必须拒绝")
        assert stub.deleted == [], "必须先验证完整列表再删除"
        assert memory.delete(["owned"], "u1") == ["owned"]
        assert stub.deleted == ["owned"]
    finally:
        memory._memory = original


def test_memory_tools_reject_empty_inputs_before_sdk_init():
    original, initialized = memory._get_memory, []
    memory._get_memory = lambda: initialized.append(True)
    try:
        for name, args in (
            ("delete", {"memory_ids": []}),
            ("search", {"query": ""}),
            ("save", {"text": "   "}),
        ):
            assert tools.call("self.memory." + name, args)["isError"] is True
        assert not initialized, "空输入不应初始化 Mem0 SDK"
    finally:
        memory._get_memory = original


def test_memory_save_reports_empty_results_accurately():
    original = memory.save
    memory.save = lambda text, user_id: {"results": []}
    try:
        response = tools._memory_save({"text": "长期偏好"})
        assert response == "没有提取到可保存的新记忆。", response
    finally:
        memory.save = original


def test_memory_tightens_existing_data_dir_permissions():
    class MemoryFactory:
        @staticmethod
        def from_config(config):
            return config

    original_dir, original_memory = memory.DATA_DIR, memory._memory
    original_key, original_module = os.environ.get("OPENAI_API_KEY"), sys.modules.get("mem0")
    with tempfile.TemporaryDirectory(prefix="dongdong-memory-") as temp_dir:
        data_dir = Path(temp_dir) / ".memory"
        data_dir.mkdir()
        data_dir.chmod(0o755)
        memory.DATA_DIR, memory._memory = data_dir, None
        os.environ["OPENAI_API_KEY"] = "test-only"
        sys.modules["mem0"] = ModuleType("mem0")
        sys.modules["mem0"].Memory = MemoryFactory
        try:
            memory._get_memory()
            assert data_dir.stat().st_mode & 0o777 == 0o700
        finally:
            memory.DATA_DIR, memory._memory = original_dir, original_memory
            if original_key is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = original_key
            if original_module is None:
                sys.modules.pop("mem0", None)
            else:
                sys.modules["mem0"] = original_module


def test_opus_roundtrip():
    import opuslib

    enc = opuslib.Encoder(16000, 1, opuslib.APPLICATION_VOIP)
    dec = opuslib.Decoder(16000, 1)
    packet = enc.encode(b"\x00\x01" * 960, 960)
    assert 0 < len(packet) < 200
    assert len(dec.decode(packet, 5760)) == 1920


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
            assert manager.resolve("run", "call", approved)
            assert not manager.resolve("run", "call", approved)
            assert await task is approved and not manager.pending()
        task = asyncio.create_task(
            manager.request("run", "cancel", "shell", {}, lambda k, d: events.append(k))
        )
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert not manager.pending() and events.count("approval_result") == 3

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
