from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "device-mac"))

from dongdong.story_schema import normalize_scene  # noqa: E402
from dongdong.story_store import StoryStore  # noqa: E402
from dongdong.story_svg import scene_to_svg  # noqa: E402


def main() -> None:
    scene = normalize_scene(
        {"elements": [{"kind": "tree"}, {"kind": "rabbit"}, {"kind": "sun"}], "durationMs": 10},
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
        {"elements": [{"kind": "<script>"}, {"kind": "tree", "label": '<&"'}, {"kind": "sun"}]},
        "x",
        "scene-003",
    )
    svg = scene_to_svg(hostile)
    assert "<script" not in svg and "&lt;&amp;&quot;" in svg and "javascript:" not in svg
    with tempfile.TemporaryDirectory() as tmp:
        store = StoryStore(Path(tmp) / "stories")
        story_id = store.create("story-1")
        assert (Path(tmp) / "stories" / story_id).stat().st_mode & 0o777 == 0o700
        store.save_scene(story_id, scene)
        assert store.load(story_id)["scenes"][0]["sceneId"] == "scene-001"
        (Path(tmp) / "stories" / story_id / "manifest.json").write_text("broken", encoding="utf-8")
        assert store.load(story_id)["manifest"]["status"] == "error"
        try:
            store.load("../escape")
        except ValueError:
            pass
        else:
            raise AssertionError("path traversal accepted")


if __name__ == "__main__":
    main()
