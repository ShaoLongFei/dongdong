import asyncio
import copy
import json
import os
import re
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "device-mac"))
os.environ["DONGDONG_HOME"] = tempfile.mkdtemp(prefix="dongdong-agent-mcp-")

from dongdong import config, tools  # noqa: E402
from dongdong.agent_service import AgentService  # noqa: E402
from dongdong.llm import StreamEvent, ToolCall  # noqa: E402

JOBS = re.compile(r"任务 ([0-9a-f]{6})")


def _cfg(workspace: Path) -> dict:
    cfg = copy.deepcopy(config.load())
    cfg["workspace"] = str(workspace)
    cfg["model"]["api_key"] = "test-key"
    cfg["runtime"] = {"max_steps": 4, "history_messages": 10}
    return cfg


def test_service_runs_job():
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp).resolve()
        seen = {"calls": 0}

        async def fake_stream(model, messages, tools):
            seen["calls"] += 1
            if seen["calls"] == 1:
                yield StreamEvent(
                    tool_calls=[
                        ToolCall(
                            "call-1",
                            "write_file",
                            '{"path": "job.txt", "content": "ok"}',
                        )
                    ]
                )
            else:
                yield StreamEvent(text="任务完成")

        service = AgentService(_cfg(workspace), stream_fn=fake_stream)
        try:
            out = service.start("写一个文件", wait_seconds=3)
            assert "完成" in out, out
            assert (workspace / "job.txt").read_text() == "ok"
            assert "任务完成" in service.result()
            assert service.current() is None
        finally:
            service.close()


def test_service_stop():
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp).resolve()

        cancelled = threading.Event()

        async def slow_stream(model, messages, tools):
            try:
                await asyncio.sleep(30)
                yield StreamEvent(text="不应该到这里")
            finally:
                cancelled.set()

        service = AgentService(_cfg(workspace), stream_fn=slow_stream)
        try:
            out = service.start("慢慢来", wait_seconds=1)
            match = JOBS.search(out)
            assert match, out
            job_id = match.group(1)
            assert "还在跑" in out
            assert "上一个任务" in service.start("第二个", wait_seconds=1)
            assert "停止" in service.stop(job_id)
            assert cancelled.wait(2), "stop 没有取消正在跑的 stream"
            assert "停止" in service.result(job_id)
        finally:
            service.close()


def test_tools_dispatch_builtin():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp).resolve()
        old_home, old_path, old_service = config.HOME, config.PATH, tools._service
        config.HOME, config.PATH = home, home / "config.json"
        config.PATH.write_text(json.dumps({"code_backend": "builtin"}), encoding="utf-8")

        class FakeService:
            def start(self, instruction, cwd=None, wait_seconds=8):
                return f"fake start: {instruction}"

            def result(self, job_id=""):
                return "fake result"

            def stop(self, job_id=""):
                return "fake stop"

        tools._service = FakeService()
        try:
            assert tools.call("self.code.run", {"instruction": "写文件"})["content"][0][
                "text"
            ].startswith("fake start:")
            assert tools.call("self.code.result", {})["content"][0]["text"] == "fake result"
            assert tools.call("self.code.stop", {})["content"][0]["text"] == "fake stop"
        finally:
            tools._service = old_service
            config.HOME, config.PATH = old_home, old_path


def test_tools_call_runtime():
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp).resolve()
        home = workspace / "home"
        home.mkdir()
        old_home, old_path, old_service = config.HOME, config.PATH, tools._service
        config.HOME, config.PATH = home, home / "config.json"
        config.PATH.write_text(
            json.dumps({"code_backend": "builtin", "workspace": str(workspace)}), encoding="utf-8"
        )

        async def fake_stream(model, messages, tools):
            if not messages:
                raise AssertionError("messages 不应为空")
            yield StreamEvent(text="mcp 完成")

        service = AgentService(_cfg(workspace), stream_fn=fake_stream)
        tools._service = service
        try:
            result = tools.call("self.code.run", {"instruction": "写 mcp.txt", "wait_seconds": 3})
            assert not result["isError"], result
            assert "完成" in result["content"][0]["text"]
            assert "mcp 完成" in tools.call("self.code.result", {})["content"][0]["text"]
        finally:
            tools._service = old_service
            config.HOME, config.PATH = old_home, old_path
            service.close()


def test_tools_default_cli():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp).resolve()
        old_home, old_path, old_service = config.HOME, config.PATH, tools._service
        old_run = tools.agent.run
        config.HOME, config.PATH = home, home / "config.json"
        config.PATH.write_text("{}", encoding="utf-8")
        tools._service = None
        tools.agent.run = lambda *args, **kwargs: "cli sentinel"
        try:
            result = tools.call("self.code.run", {"instruction": "x"})
            assert result["content"][0]["text"] == "cli sentinel"
            assert not result["isError"]
        finally:
            tools.agent.run = old_run
            tools._service = old_service
            config.HOME, config.PATH = old_home, old_path


def test_service_rejects_cwd_outside_workspace():
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp).resolve()
        service = AgentService(_cfg(workspace), stream_fn=lambda *args: None)
        try:
            try:
                service.start("越界", cwd="/tmp")
            except ValueError as exc:
                assert "工作区" in str(exc)
            else:
                raise AssertionError("应当拒绝工作区外的 cwd")
        finally:
            service.close()


if __name__ == "__main__":
    test_service_runs_job()
    print("ok   test_service_runs_job")
    test_service_stop()
    print("ok   test_service_stop")
    test_tools_dispatch_builtin()
    print("ok   test_tools_dispatch_builtin")
    test_tools_call_runtime()
    print("ok   test_tools_call_runtime")
    test_tools_default_cli()
    print("ok   test_tools_default_cli")
    test_service_rejects_cwd_outside_workspace()
    print("ok   test_service_rejects_cwd_outside_workspace")
    print("agent mcp 通过")
