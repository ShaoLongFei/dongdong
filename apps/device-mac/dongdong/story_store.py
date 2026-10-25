"""故事目录的安全持久化。"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from .story_svg import scene_to_svg


class StoryModeIn(BaseModel):
    enabled: bool = Field(strict=True)


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_SCENE_NUMBER = re.compile(r"^scene-(\d+)$")


def _ordered_scene_ids(ids: list[object]) -> list[str]:
    valid = [item for item in ids if isinstance(item, str) and _ID.fullmatch(item)]
    numeric = []
    other = []
    for index, scene_id in enumerate(valid):
        match = _SCENE_NUMBER.fullmatch(scene_id)
        (numeric if match else other).append(
            (int(match.group(1)), index, scene_id) if match else (index, scene_id)
        )
    numeric.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in numeric] + [item[1] for item in other]


class StoryStore:
    def __init__(self, root: Path):
        self.root = Path(root).expanduser()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)

    def _validate(self, story_id: str) -> Path:
        if not isinstance(story_id, str) or not _ID.fullmatch(story_id):
            raise ValueError("非法故事 ID")
        path = self.root / story_id
        if path.exists() and path.is_symlink():
            raise ValueError("故事目录不能是符号链接")
        resolved_root = self.root.resolve()
        resolved = path.resolve()
        if resolved.parent != resolved_root:
            raise ValueError("故事路径越界")
        return path

    @staticmethod
    def _atomic(path: Path, content: str) -> None:
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
            path.chmod(0o600)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def create(self, story_id: str | None = None, title: str = "新故事") -> str:
        story_id = story_id or f"story-{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}"
        directory = self._validate(story_id)
        directory.mkdir(mode=0o700)
        manifest = {
            "storyId": story_id,
            "title": title.strip() or "新故事",
            "status": "recording",
            "width": 1280,
            "height": 720,
            "scenes": [],
            "createdAt": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        self.save_manifest(story_id, manifest)
        return story_id

    def save_manifest(self, story_id: str, manifest: dict) -> None:
        directory = self._validate(story_id)
        if not directory.is_dir():
            raise ValueError("故事不存在")
        self._atomic(
            directory / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2)
        )

    def save_scene(self, story_id: str, scene: dict) -> None:
        directory = self._validate(story_id)
        if not directory.is_dir():
            raise ValueError("故事不存在")
        scene_id = str(scene.get("sceneId") or "")
        if not _ID.fullmatch(scene_id):
            raise ValueError("非法场景 ID")
        self._atomic(
            directory / f"{scene_id}.json", json.dumps(scene, ensure_ascii=False, indent=2)
        )
        self._atomic(directory / f"{scene_id}.svg", scene_to_svg(scene))
        manifest = self.load(story_id)["manifest"]
        ids = [item for item in manifest.get("scenes", []) if item != scene_id]
        ids.append(scene_id)
        manifest["scenes"] = _ordered_scene_ids(ids)
        self.save_manifest(story_id, manifest)

    def load(self, story_id: str) -> dict:
        directory = self._validate(story_id)
        if not directory.is_dir():
            raise ValueError("故事不存在")
        manifest_path = directory / "manifest.json"
        if manifest_path.is_symlink():
            raise ValueError("manifest 不能是符号链接")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                raise ValueError
        except (FileNotFoundError, json.JSONDecodeError, ValueError):
            scene_ids = _ordered_scene_ids(
                [path.stem for path in directory.glob("scene-*.json") if not path.is_symlink()]
            )
            manifest = {
                "storyId": story_id,
                "title": "恢复的故事",
                "status": "error",
                "width": 1280,
                "height": 720,
                "scenes": scene_ids,
            }
        scenes = []
        for scene_id in _ordered_scene_ids(manifest.get("scenes", [])):
            if isinstance(scene_id, str) and _ID.fullmatch(scene_id):
                try:
                    scene_path = directory / f"{scene_id}.json"
                    if not scene_path.is_symlink():
                        scenes.append(json.loads(scene_path.read_text(encoding="utf-8")))
                except (FileNotFoundError, json.JSONDecodeError):
                    continue
        return {"manifest": manifest, "scenes": scenes}

    def list_stories(self) -> list[dict]:
        result = []
        for path in self.root.iterdir():
            if path.is_dir() and not path.is_symlink() and _ID.fullmatch(path.name):
                try:
                    result.append(self.load(path.name)["manifest"])
                except ValueError:
                    continue
        return sorted(result, key=lambda item: item.get("createdAt", ""), reverse=True)


def router(state):
    from fastapi import APIRouter, HTTPException

    routes = APIRouter(prefix="/api")

    @routes.put("/story/mode")
    async def mode(body: StoryModeIn):
        try:
            return await state.story_mode(body.enabled)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @routes.get("/story/state")
    async def current():
        return state.story.state()

    @routes.get("/stories")
    async def stories():
        return {"stories": state.story.store.list_stories()}

    @routes.get("/stories/{story_id}")
    async def story(story_id: str):
        try:
            return state.story.store.load(story_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from None

    @routes.post("/stories/{story_id}/replay")
    async def replay(story_id: str):
        try:
            scenes = state.story.replay(story_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from None
        return {"storyId": story_id, "scenes": scenes, "cursor": state.story.state()["cursor"]}

    return routes
