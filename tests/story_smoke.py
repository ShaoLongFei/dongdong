from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "device-mac"))

from dongdong.story_schema import normalize_scene
from dongdong.story_store import StoryStore
from dongdong.story_svg import scene_to_svg


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
    assert (
        "<script" not in svg and "&lt;&amp;&quot;" in svg and "javascript:" not in svg
    )
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
    assert (
        'x="0"' in direct
        and 'width="1280"' in direct
        and 'data-duration-ms="30000"' in direct
    )
    assert direct.count("<g ") == 1
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
        assert (
            Path(tmp) / "stories" / story_id / "scene-1.json"
        ).stat().st_mode & 0o777 == 0o600
        (Path(tmp) / "stories" / story_id / "manifest.json").write_text(
            "broken", encoding="utf-8"
        )
        assert store.load(story_id)["manifest"]["status"] == "error"
        (Path(tmp) / "stories" / story_id / "manifest.json").unlink()
        (Path(tmp) / "stories" / story_id / "manifest.json").symlink_to(
            Path(tmp) / "outside.json"
        )
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


if __name__ == "__main__":
    main()
