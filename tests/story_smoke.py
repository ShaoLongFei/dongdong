from __future__ import annotations

import asyncio
import platform
import re
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "device-mac"))

from dongdong.story import StorySession, story_for  # noqa: E402
from dongdong.story_export import bundled_ffmpeg, export_story  # noqa: E402
from dongdong.story_schema import normalize_scene  # noqa: E402
from dongdong.story_store import StoryStore  # noqa: E402
from dongdong.story_svg import scene_to_svg  # noqa: E402
from dongdong.web_runtime import WebState  # noqa: E402


def _probe_mp4(ffmpeg: Path, path: Path) -> tuple[float, bool]:
    result = subprocess.run(
        [str(ffmpeg), "-hide_banner", "-i", str(path), "-f", "null", "-"],
        capture_output=True,
        text=True,
        check=False,
    )
    output = result.stderr + result.stdout
    match = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", output)
    return (
        float(match.group(1)) * 3600 + float(match.group(2)) * 60 + float(match.group(3)),
        bool(re.search(r"Stream #\d+:\d+.*Audio", output)),
    )


def _api_integration_smoke() -> None:
    """通过 ASGI 走一遍故事路由，并确认故事结束后普通会话仍可提交。"""
    import httpx
    from dongdong import config as app_config
    from dongdong.web_server import create_app

    old_home, old_path = app_config.HOME, app_config.PATH
    with tempfile.TemporaryDirectory(prefix="dongdong-web-story-") as tmp:
        home = Path(tmp) / "home"
        workspace = Path(tmp) / "workspace"
        home.mkdir()
        workspace.mkdir()
        app_config.HOME = home
        app_config.PATH = home / "config.json"
        cfg = app_config.load()
        cfg["workspace"] = str(workspace)
        cfg["model"]["api_key"] = "story-smoke-only"
        app = create_app(cfg)

        async def check() -> None:
            async with app.router.lifespan_context(app):
                state = app.state.web_state
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
                ) as client:
                    created = await client.post("/api/sessions", json={"title": "故事接口"})
                    assert created.status_code == 200, created.text
                    sid = created.json()["id"]

                    listed = await client.get("/api/stories")
                    assert listed.status_code == 200 and listed.json()["stories"] == []

                    started = await client.put("/api/story/mode", json={"enabled": True})
                    assert started.status_code == 200, started.text
                    story_id = started.json()["storyId"]
                    assert started.json()["status"] == "recording"
                    state_view = await client.get("/api/story/state")
                    assert state_view.status_code == 200
                    assert state_view.json()["storyId"] == story_id

                    # 直接写入一幕，避免集成 smoke 依赖模型网络请求。
                    state.story.store.save_scene(
                        story_id,
                        normalize_scene(
                            {"elements": [{"kind": "tree"}, {"kind": "sun"}]},
                            "接口场景",
                            "scene-001",
                        ),
                    )
                    stopped = await client.put("/api/story/mode", json={"enabled": False})
                    assert stopped.status_code == 200, stopped.text
                    assert stopped.json()["status"] == "done"

                    listed = await client.get("/api/stories")
                    assert listed.status_code == 200
                    assert any(item["storyId"] == story_id for item in listed.json()["stories"])

                    export = await client.post(
                        f"/api/stories/{story_id}/exports", json={"format": "png"}
                    )
                    assert export.status_code == 200, export.text
                    job_id = export.json()["job_id"]
                    for _ in range(300):
                        job = await client.get(f"/api/stories/{story_id}/exports/{job_id}")
                        assert job.status_code == 200, job.text
                        if job.json()["status"] != "running":
                            break
                        await asyncio.sleep(0.01)
                    assert job.json()["status"] in {"done", "error"}

                    # 故事停止后普通消息仍走普通任务入口；这里不触发模型执行。
                    state.manager.submit = lambda *_args, **_kwargs: "normal-smoke-run"
                    message = await client.post(
                        f"/api/sessions/{sid}/messages", json={"text": "普通消息"}
                    )
                    assert message.status_code == 200, message.text
                    assert message.json()["run_id"] == "normal-smoke-run"

        try:
            asyncio.run(check())
        finally:
            app_config.HOME, app_config.PATH = old_home, old_path


def main() -> None:
    _api_integration_smoke()
    scene = normalize_scene(
        {
            "elements": [{"kind": "tree"}, {"kind": "rabbit"}, {"kind": "sun"}],
            "durationMs": 10,
        },
        "小兔子",
        "scene-001",
    )
    assert (
        len(scene["elements"]) == 3
        and scene["elements"][1]["startMs"] >= scene["elements"][0]["durationMs"]
    )
    fallback = normalize_scene("{bad", "默认故事", "scene-002")
    assert len(fallback["elements"]) == 3
    hostile = normalize_scene(
        {
            "elements": [
                {"kind": "<script>"},
                {"kind": "tree", "label": '<&"'},
                {"kind": "sun"},
            ]
        },
        "x",
        "scene-003",
    )
    svg = scene_to_svg(hostile)
    assert "<script" not in svg and "&lt;&amp;&quot;" in svg and "javascript:" not in svg
    direct = scene_to_svg(
        {
            "elements": [
                {
                    "kind": "<script>",
                    "color": 'url("https://evil.invalid/x")',
                    "x": "javascript:alert(1)",
                    "y": -99,
                    "width": 99999,
                    "height": "bad",
                    "startMs": "bad",
                    "durationMs": 999999,
                    "label": '<img onerror="alert(1)">',
                    "onclick": "alert(1)",
                }
            ]
        }
    )
    assert all(
        token not in direct
        for token in (
            "<script",
            "url(",
            "javascript:",
            "<img",
            "<g onerror",
            "<g onclick",
        )
    )
    assert 'x="0"' in direct and 'width="1280"' in direct and 'data-duration-ms="30000"' in direct
    assert direct.count("<g ") == 1
    hostile_types = scene_to_svg({"elements": [{"kind": [], "color": []}]})
    assert "<script" not in hostile_types and "url(" not in hostile_types
    with tempfile.TemporaryDirectory() as tmp:
        store = StoryStore(Path(tmp) / "stories")
        story_id = store.create("story-1")
        assert (Path(tmp) / "stories" / story_id).stat().st_mode & 0o777 == 0o700
        store.save_scene(story_id, scene)
        assert store.load(story_id)["scenes"][0]["sceneId"] == "scene-001"
        has_ffmpeg = False
        if platform.system() == "Darwin":
            try:
                bundled_ffmpeg()
            except RuntimeError as exc:
                if "不可用" not in str(exc):
                    raise
            else:
                has_ffmpeg = True
        if platform.system() == "Darwin" and all(
            Path(tool).is_file() for tool in ("/usr/bin/qlmanage", "/usr/bin/sips")
        ):
            png = export_story(Path(tmp) / "stories" / story_id, "png")
            pdf = export_story(Path(tmp) / "stories" / story_id, "pdf")
            assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
            assert struct.unpack(">II", png.read_bytes()[16:24]) == (1280, 776)
            assert pdf.read_bytes()[:5] == b"%PDF-"
            store.save_scene(story_id, {**scene, "sceneId": "scene-002"})
            storyboard_png = export_story(Path(tmp) / "stories" / story_id, "png")
            storyboard_pdf = export_story(Path(tmp) / "stories" / story_id, "pdf")
            assert struct.unpack(">II", storyboard_png.read_bytes()[16:24]) == (
                1280,
                1552,
            )
            assert storyboard_pdf.read_bytes()[:5] == b"%PDF-"
        else:
            print("story export smoke skipped: macOS qlmanage/sips unavailable")
        if has_ffmpeg:
            mp4 = export_story(Path(tmp) / "stories" / story_id, "mp4")
            assert mp4.read_bytes()[4:8] == b"ftyp"
            duration, has_audio = _probe_mp4(bundled_ffmpeg(), mp4)
            scene_count = len(store.load(story_id)["scenes"])
            assert scene_count * 2.5 <= duration <= scene_count * 3.2
            assert has_audio
        for scene_id in ("scene-10", "scene-2", "scene-1"):
            store.save_scene(story_id, {**scene, "sceneId": scene_id})
        assert [item["sceneId"] for item in store.load(story_id)["scenes"]] == [
            "scene-001",
            "scene-1",
            "scene-002",
            "scene-2",
            "scene-10",
        ]
        assert (Path(tmp) / "stories" / story_id / "scene-1.json").stat().st_mode & 0o777 == 0o600
        (Path(tmp) / "stories" / story_id / "manifest.json").write_text("broken", encoding="utf-8")
        assert store.load(story_id)["manifest"]["status"] == "error"
        (Path(tmp) / "stories" / story_id / "manifest.json").unlink()
        (Path(tmp) / "stories" / story_id / "manifest.json").symlink_to(Path(tmp) / "outside.json")
        try:
            store.load(story_id)
        except ValueError:
            pass
        else:
            raise AssertionError("manifest symlink accepted")
        outside = Path(tmp) / "outside-scene.json"
        outside.write_text('{"sceneId":"outside"}', encoding="utf-8")
        (Path(tmp) / "stories" / story_id / "scene-99.json").symlink_to(outside)
        (Path(tmp) / "stories" / story_id / "manifest.json").unlink()
        recovered = store.load(story_id)
        assert all(item["sceneId"] != "outside" for item in recovered["scenes"])
        store.save_manifest(story_id, recovered["manifest"])
        try:
            store.load("../escape")
        except ValueError:
            pass
        else:
            raise AssertionError("path traversal accepted")

        assert store.exports.submit
        try:
            store.exports.submit(story_id, "gif")
        except ValueError:
            pass
        else:
            raise AssertionError("invalid export format accepted")
        missing_job = store.exports.status("missing")
        assert missing_job["status"] == "missing"
        job_id = store.exports.submit(story_id, "mp4")
        for _ in range(2000):
            job = store.exports.status(job_id)
            if job["status"] != "running":
                break
            import time

            time.sleep(0.01)
        if has_ffmpeg:
            assert job["status"] == "done"
            assert Path(job["outputPath"]).read_bytes()[4:8] == b"ftyp"
        else:
            assert job["status"] == "error"
            assert job.get("error")
        assert not (Path(tmp) / "stories" / story_id / "scene-1.svg").is_symlink()

        class BrokenModel:
            async def generate_scene(self, _prompt):
                raise RuntimeError("model unavailable")

        async def session_smoke() -> None:
            session = StorySession(BrokenModel(), store=store, flush_seconds=60)
            story_id = session.start()
            session.accept_text("小兔子看见太阳于是跑向森林" * 4)
            assert session.state()["bufferedText"]
            result = await session.stop()
            assert result["status"] == "error"
            assert store.load(story_id)["scenes"] == []
            events = session.events(0)
            assert events and session.events(events[-1]["id"]) == []
            assert any(event["type"] == "error" for event in events)
            assert not any(event["type"] == "scene" for event in events)
            await session.close()

        async def buffering_smoke() -> None:
            event_word = StorySession(BrokenModel(), store=store, flush_seconds=60)
            event_word.start()
            event_word.accept_text("小兔子于是")
            await asyncio.sleep(0.01)
            assert event_word.state()["bufferedText"] == ""
            assert event_word.state()["sceneCount"] == 1
            await event_word.close()

            fixed_buffer = StorySession(BrokenModel(), store=store, flush_seconds=60)
            fixed_buffer.start()
            fixed_buffer.accept_text("甲" * 40)
            assert fixed_buffer.state()["bufferedText"] == ""
            assert fixed_buffer.state()["sceneCount"] == 1
            await fixed_buffer.close()

        async def lifecycle_smoke() -> None:
            pending = StorySession(BrokenModel(), store=store, flush_seconds=60)
            pending_id = pending.start()
            pending._pending["scene-001"] = "停止时仍未完成"
            result = await pending.stop()
            assert result["status"] == "error"
            assert pending._pending == {}
            assert store.load(pending_id)["scenes"] == []

            restored_id = store.create("story-restored")
            store.save_scene(restored_id, {**scene, "sceneId": "scene-004"})
            manifest = store.load(restored_id)["manifest"]
            manifest.update({"status": "recording", "sessionId": "chat-history"})
            store.save_manifest(restored_id, manifest)
            host = SimpleNamespace(
                model=None,
                stories={},
                story=SimpleNamespace(store=store),
            )
            restored = story_for(host, restored_id)
            assert restored.status == "done"
            assert restored.state()["sceneCount"] == 4
            events = restored.events(0)
            assert events[0]["data"]["scene"]["sceneId"] == "scene-004"
            assert events[0]["data"]["replay"] is True
            assert story_for(host, restored_id) is restored

        asyncio.run(session_smoke())
        asyncio.run(buffering_smoke())
        asyncio.run(lifecycle_smoke())

        # 故事中的 STT 只进入故事缓冲区，命令也不应污染普通会话消息。
        class MemoryStore:
            def __init__(self):
                self.rows = []

            def get_session(self, _sid):
                return {"archived_at": None}

            def append_message(self, _sid, role, text, source=""):
                self.rows.append((role, text, source))

        class Meeting:
            class db:
                @staticmethod
                def active(_sid):
                    return None

            def voice(self, _text):
                raise AssertionError("故事语音不应进入会议")

        async def voice_route_smoke() -> None:
            host = object.__new__(WebState)
            host.task_session = "chat-1"
            host.current_project = None
            host._project_tasks = {}
            host.stories = {}
            host.store = MemoryStore()
            host.meeting = Meeting()
            host.loop = asyncio.get_running_loop()
            host.model = None
            host.story_store = store
            host.session_busy = lambda _sid: False
            host.recover_stories = list
            story = StorySession(None, store=store)
            story.session_id = "chat-1"
            story.start()
            host.stories["chat-1"] = story
            host.voice_text("mic", "user", "小兔子走进森林")
            assert story.state()["bufferedText"]
            assert host.store.rows == []
            host.voice_text("mic", "user", "请停止讲故事")
            await asyncio.sleep(0)
            assert host.store.rows == []
            await story.close()

        asyncio.run(voice_route_smoke())

    assert WebState._story_command("请开始讲故事") == "start"
    assert WebState._story_command("进入故事模式吧") == "start"
    assert WebState._story_command("请停止讲故事") == "stop"
    assert WebState._story_command("退出故事模式") == "stop"
    assert WebState._story_command("讲个故事") is None


if __name__ == "__main__":
    main()
