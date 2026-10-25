from __future__ import annotations

# ruff: noqa: I001 -- 先导入 smoke，初始化临时 HOME 和设备端模块路径。

from smoke import (
    ROOT,
    asyncio,
    config,
    contextlib,
    tempfile,
    Path,
    SimpleNamespace,
    patch,
    tools,
    mcp,
)
import json
from dongdong.meeting import Meeting
from dongdong.store import Store


def test_unified_voice_session_preserves_execution_boundaries():
    from dongdong.web_runtime import WebState
    from dongdong.projects import use_session
    from dongdong.runtime import Runtime
    from dongdong.llm import ModelConfig

    async def check():
        cfg = config.load()
        cfg["workspace"], cfg["model"]["api_key"] = str(config.HOME), "boundary-only"
        host = WebState(cfg)
        sid = host.new_session("统一对话", "web", None)
        before = len(host.store.list_sessions(limit=100))
        host.voice_text("first", "user", "看看这个项目")
        host.voice_text("reconnected", "assistant", "按这个方案实施")
        assert host.task_session == sid
        rows = host.store.messages(sid)
        assert [row["source"] for row in rows] == ["voice", "voice"]
        assert len(host.store.list_sessions(limit=100)) == before
        runtime = Runtime(host.store, ModelConfig("", "", ""), config.HOME)
        assert len(runtime._messages(sid)) == 1, "语音口播不能成为模型指令或完成事实"
        host.meeting.db.open(sid, str(config.HOME))
        host.voice_text("meeting", "user", "补充会议意见")
        host.voice_text("meeting", "assistant", "按这个方案实施")
        assert host.meeting.db.get(sid)["seq"] == 1
        assert host.store.messages(sid)[-1]["content"] == "按这个方案实施"
        assert len(runtime._messages(sid)) == 1
        host.meeting.db.close(sid)
        pid = host.store.create_project("隔离项目", str(config.HOME))
        other = host.new_session("其他项目", "web", pid)
        use_session(host, other)
        host.voice_text("other", "user", "隔离意见")
        assert host.store.messages(other)[-1]["content"] == "隔离意见"
        use_session(host, sid)
        host.voice_text("back", "assistant", "回到原对话")
        assert host.store.messages(sid)[-1]["content"] == "回到原对话"
        host.store.update_session(other, {"archived_at": "2026-10-25"})
        try:
            use_session(host, other)
        except ValueError:
            pass
        else:
            raise AssertionError("归档会话不能绑定语音")
        await host.close()

    asyncio.run(check())


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
        meeting = Meeting.__new__(Meeting)
        meeting.db, meeting.notice = db, ""
        status = meeting.mcp_status()
        assert json.loads(status)["workspace"] == tmp
        assert json.loads(status)["discussion_count"] == 1
        service = SimpleNamespace(meeting_status=meeting.mcp_status)
        with patch.object(tools, "_service", service):
            for name in ("self.system.status", "self.code.run", "self.code.result"):
                assert tools.call(name, {})["content"][0]["text"] == status
            listed = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
            assert all(
                tmp in item["description"]
                for item in listed["result"]["tools"]
                if item["name"] in {"self.system.status", "self.code.run", "self.code.result"}
            )
        db.close(sid)
        with patch.object(tools, "_service", service):
            listed = mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            assert all(tmp not in item["description"] for item in listed["result"]["tools"])
        db.open(sid, tmp)
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
    for name, test in sorted(globals().copy().items()):
        if name.startswith("test_"):
            test()
            print(f"ok   {name}")
