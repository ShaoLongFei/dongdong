"""实时故事会话：把语音片段整理成受控场景并持久化。"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable
from datetime import UTC, datetime
from pathlib import Path

from .llm import ModelConfig, stream_chat
from .story_schema import normalize_scene
from .story_store import StoryStore
from .story_svg import scene_to_svg

log = logging.getLogger("dongdong.story")
_WORDS = re.compile(r"[。！？!?；;\n]+|然后|突然|于是|看见|跑向|拿起|变成")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class StorySession:
    """一个录音故事及其场景生成任务。"""

    def __init__(
        self,
        model: ModelConfig | object | None = None,
        store: StoryStore | None = None,
        root: Path | None = None,
        *,
        flush_seconds: float = 2.0,
        max_tasks: int = 2,
    ):
        self.model = model
        self.store = store or StoryStore(root or Path.home() / ".dongdong" / "stories")
        self.flush_seconds = flush_seconds
        self.story_id = ""
        self.status = "idle"
        self._buffer = ""
        self._timer: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()
        self._sem = asyncio.Semaphore(max(1, max_tasks))
        self._scene_number = 0
        self._events: list[dict] = []
        self._cursor = 0
        self._characters: list[dict] = []
        self._lock = asyncio.Lock()
        self._pending: dict[str, str] = {}
        self._error = ""

    def start(self) -> str:
        if self.status in {"recording", "stopping"}:
            return self.story_id
        self.story_id = self.store.create(title="新故事")
        self.status = "recording"
        self._buffer = ""
        self._events = []
        self._cursor = 0
        self._scene_number = 0
        self._characters = []
        self._pending = {}
        self._error = ""
        self._publish("state", {"status": self.status, "storyId": self.story_id})
        return self.story_id

    def accept_text(self, text: str) -> None:
        if self.status != "recording" or not str(text or "").strip():
            return
        self._buffer += " " + str(text).strip()
        while len(self._buffer.strip()) >= 40:
            chunk, self._buffer = self._buffer.strip()[:40], self._buffer.strip()[40:]
            self._submit(chunk)
        if _WORDS.search(self._buffer):
            self._schedule_flush()
        elif self._timer is None or self._timer.done():
            try:
                self._timer = asyncio.create_task(self._delayed_flush())
            except RuntimeError:
                # 同步调用方（例如单元测试或设备重连窗口）由 stop() 收尾。
                self._timer = None

    async def _delayed_flush(self) -> None:
        await asyncio.sleep(self.flush_seconds)
        self._flush()

    def _schedule_flush(self) -> None:
        if self._timer and not self._timer.done():
            self._timer.cancel()
        try:
            self._timer = asyncio.create_task(self._flush_soon())
        except RuntimeError:
            self._timer = None

    async def _flush_soon(self) -> None:
        await asyncio.sleep(0)
        self._flush()

    def _flush(self) -> None:
        text = self._buffer.strip()
        self._buffer = ""
        self._submit(text)

    def _submit(self, text: str) -> None:
        if not text or self.status != "recording":
            return
        self._scene_number += 1
        scene_id = f"scene-{self._scene_number:03d}"
        self._pending[scene_id] = text
        task = asyncio.create_task(self._generate(scene_id, text))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _generate(self, scene_id: str, text: str) -> None:
        async with self._sem:
            try:
                raw = await self._request_scene(text)
                scene = normalize_scene(raw, text, scene_id)
                self._characters = scene.get("characters", [])
                self.store.save_scene(self.story_id, scene)
                scene["svg"] = scene_to_svg(scene)
                self._publish("scene", {"storyId": self.story_id, "scene": scene})
            except Exception as exc:  # model and disk errors are visible in the event stream
                log.warning("故事场景失败: %s", exc)
                scene = normalize_scene({}, text, scene_id)
                try:
                    self.store.save_scene(self.story_id, scene)
                    scene["svg"] = scene_to_svg(scene)
                    self._publish(
                        "scene", {"storyId": self.story_id, "scene": scene, "fallback": True}
                    )
                except Exception as save_exc:
                    self._error = str(save_exc)
                    self.status = "error"
                    self._publish("error", {"message": str(save_exc)})
                self._publish("error", {"message": str(exc), "sceneId": scene_id})
            finally:
                self._pending.pop(scene_id, None)

    async def _request_scene(self, text: str) -> dict | str:
        prompt = (
            "把下面的儿童故事片段转换为 JSON 场景。只输出 JSON，不要 Markdown。"
            "字段为 elements（至少 3 个受控元素）、characters、durationMs、storyBasis。"
            f"\n前文角色：{json.dumps(self._characters, ensure_ascii=False)}\n片段：{text}"
        )
        if self.model is None:
            raise RuntimeError("模型未配置")
        if hasattr(self.model, "generate_scene"):
            result = self.model.generate_scene(prompt)
            return await result if isinstance(result, Awaitable) else result
        messages = [
            {"role": "system", "content": "你是儿童故事分镜生成器，只返回 JSON。"},
            {"role": "user", "content": prompt},
        ]
        chunks = []
        async for event in stream_chat(self.model, messages, []):
            chunks.append(event.text)
        return "".join(chunks)

    async def stop(self) -> dict:
        if self.status == "idle":
            return self.state()
        self.status = "stopping"
        if self._timer and not self._timer.done():
            self._timer.cancel()
            await asyncio.gather(self._timer, return_exceptions=True)
        self._flush()
        if self._tasks:
            _, waiting = await asyncio.wait(set(self._tasks), timeout=5)
            for task in waiting:
                task.cancel()
            if waiting:
                await asyncio.gather(*waiting, return_exceptions=True)
                for scene_id, text in list(self._pending.items()):
                    scene = normalize_scene({}, text, scene_id)
                    try:
                        self.store.save_scene(self.story_id, scene)
                        self._publish("scene", {"storyId": self.story_id, "scene": scene, "fallback": True})
                    except Exception as exc:
                        self._error = str(exc)
                        self.status = "error"
                        self._publish("error", {"message": str(exc), "sceneId": scene_id})
                    self._pending.pop(scene_id, None)
        if self.status != "error":
            self.status = "done"
        if self.story_id:
            data = self.store.load(self.story_id)
            manifest = data["manifest"]
            manifest["status"] = self.status
            manifest["updatedAt"] = datetime.now(UTC).isoformat(timespec="seconds")
            self.store.save_manifest(self.story_id, manifest)
        self._publish("state", {"status": self.status, "storyId": self.story_id})
        if self.status != "error":
            self._publish("export", {"storyId": self.story_id})
        return self.state()

    def state(self) -> dict:
        return {
            "storyId": self.story_id,
            "status": self.status,
            "bufferedText": self._buffer,
            "sceneCount": self._scene_number,
            "cursor": self._cursor,
        }

    def events(self, since: int = 0) -> list[dict]:
        try:
            cursor = max(0, int(since))
        except (TypeError, ValueError):
            cursor = 0
        return [item for item in self._events if item["id"] > cursor]

    def replay(self, story_id: str) -> list[dict]:
        """重新发布已保存场景，供断线后的画布恢复。"""
        data = self.store.load(story_id)
        for scene in data["scenes"]:
            self._publish("scene", {"storyId": story_id, "scene": scene, "replay": True})
        return data["scenes"]

    def _publish(self, kind: str, data: dict) -> None:
        self._cursor += 1
        self._events.append({"id": self._cursor, "type": kind, "data": data})

    async def close(self) -> None:
        if self.status == "recording":
            await self.stop()
        elif self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
