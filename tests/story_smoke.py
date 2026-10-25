from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "device-mac"))

from dongdong.story import StorySession, story_for
from dongdong.story_schema import normalize_scene
from dongdong.story_store import StoryStore
from dongdong.story_svg import scene_to_svg
from dongdong.web_runtime import WebState


def main() -> None:
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
        for scene_id in ("scene-10", "scene-2", "scene-1"):
            store.save_scene(story_id, {**scene, "sceneId": scene_id})
        assert [item["sceneId"] for item in store.load(story_id)["scenes"]] == [
            "scene-001",
            "scene-1",
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
        for _ in range(100):
            job = store.exports.status(job_id)
            if job["status"] != "running":
                break
            import time

            time.sleep(0.01)
        assert job["status"] == "error"
        assert (
            job.get("error") and not (Path(tmp) / "stories" / story_id / "scene-1.svg").is_symlink()
        )

        class BrokenModel:
            async def generate_scene(self, _prompt):
                raise RuntimeError("model unavailable")

        async def session_smoke() -> None:
            session = StorySession(BrokenModel(), store=store, flush_seconds=60)
            story_id = session.start()
            session.accept_text("小兔子看见太阳于是跑向森林" * 4)
            assert session.state()["bufferedText"]
            result = await session.stop()
            assert result["status"] == "done"
            assert store.load(story_id)["scenes"]
            events = session.events(0)
            assert events and session.events(events[-1]["id"]) == []
            assert any(event["type"] == "export" for event in events)
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
            assert result["status"] == "done"
            assert pending._pending == {}
            assert store.load(pending_id)["scenes"]

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

    assert WebState._story_command("请开始讲故事") == "start"
    assert WebState._story_command("进入故事模式吧") == "start"
    assert WebState._story_command("请停止讲故事") == "stop"
    assert WebState._story_command("退出故事模式") == "stop"
    assert WebState._story_command("讲个故事") is None


if __name__ == "__main__":
    main()
