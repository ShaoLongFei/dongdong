from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable
from datetime import UTC, datetime
from pathlib import Path

from .llm import ModelConfig, stream_chat
from .story_schema import normalize_scenes
from .story_store import StoryStore
from .story_svg import scene_to_svg

log = logging.getLogger("dongdong.story")
_CHARACTER_COLORS = {
    "阿栗": "#E87824",
    "米米": "#D9B38C",
    "团团": "#9B6B3D",
    "圆圆": "#8B6F47",
    "啄木鸟": "#8B4513",
}
_BOUNDARY = re.compile(r"[。！？!?；;\n]")
_MIN_CHUNK = 36
_MAX_CHUNK = 180


class StorySession:
    def __init__(
        self,
        model: ModelConfig | object | None = None,
        store: StoryStore | None = None,
        root: Path | None = None,
        *,
        flush_seconds: float = 2.0,
        max_tasks: int = 1,
    ):
        self.model = model
        self.store = store or StoryStore(root or Path.home() / ".dongdong" / "stories")
        self.flush_seconds = flush_seconds
        self.story_id = ""
        self.session_id = ""
        self.status = "idle"
        self._buffer = ""
        self._timer: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()
        self._sem = asyncio.Semaphore(max(1, max_tasks))
        self._scene_number = 0
        self._batch_number = 0
        self._events: list[dict] = []
        self._cursor = 0
        self._characters: list[dict] = []
        self._character_styles: dict[str, tuple[str, str]] = {}
        self._story_so_far = ""
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
        self._batch_number = 0
        self._characters = []
        self._character_styles = {}
        self._story_so_far = ""
        self._pending = {}
        self._error = ""
        self._publish("state", {"status": self.status, "storyId": self.story_id})
        return self.story_id

    def accept_text(self, text: str) -> None:
        if self.status != "recording" or not str(text or "").strip():
            return
        text = str(text).strip()
        self._buffer += (" " if self._buffer else "") + text
        while chunk := self._next_chunk():
            self._submit(chunk)
        if _BOUNDARY.search(self._buffer) and len(self._buffer.strip()) >= _MIN_CHUNK:
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
        while chunk := self._next_chunk(force=True):
            self._submit(chunk)

    def _next_chunk(self, force: bool = False) -> str:
        text = self._buffer.strip()
        if not text:
            return ""
        boundary = next(
            (
                match.end()
                for match in _BOUNDARY.finditer(text)
                if _MIN_CHUNK <= match.end() <= _MAX_CHUNK
            ),
            None,
        )
        if boundary is None and len(text) < _MAX_CHUNK and not force:
            return ""
        cut = boundary or min(len(text), _MAX_CHUNK)
        if boundary is None and cut == _MAX_CHUNK:
            comma = max(text.rfind("，", 70, cut), text.rfind(",", 70, cut))
            if comma >= _MIN_CHUNK:
                cut = comma + 1
        self._buffer = text[cut:].lstrip()
        return text[:cut].strip()

    def _submit(self, text: str) -> None:
        if not text or self.status != "recording":
            return
        self._batch_number += 1
        batch_id = f"batch-{self._batch_number:03d}"
        self._pending[batch_id] = text
        task = asyncio.create_task(self._generate(batch_id, text))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _generate(self, batch_id: str, text: str) -> None:
        async with self._sem:
            try:
                raw = await self._request_scene(text)
                scenes = normalize_scenes(raw, text, self._scene_number + 1)
                self._scene_number += len(scenes)
                for scene in scenes:
                    for element in scene["elements"]:
                        label = str(element.get("label") or "")
                        key = next(
                            (
                                token
                                for token in ("阿栗", "米米", "团团", "圆圆", "啄木鸟")
                                if token in label
                            ),
                            "",
                        )
                        if not key or element["kind"] not in {
                            "fox",
                            "rabbit",
                            "hedgehog",
                            "bird",
                            "person",
                        }:
                            continue
                        style = self._character_styles.setdefault(
                            key, (element["kind"], _CHARACTER_COLORS.get(key, element["color"]))
                        )
                        element["kind"], element["color"] = style
                    self._characters = scene.get("characters", [])
                    self.store.save_scene(self.story_id, scene)
                    scene["svg"] = scene_to_svg(scene)
                    self._publish("scene", {"storyId": self.story_id, "scene": scene})
                self._story_so_far = (self._story_so_far + " " + text).strip()[-1600:]
                self._pending.pop(batch_id, None)
            except Exception as exc:  # model and disk errors are visible in the event stream
                log.warning("故事场景失败: %s", exc)
                self._pending.pop(batch_id, None)
                self._error = str(exc)
                self.status = "error"
                self._publish("error", {"message": str(exc), "batchId": batch_id})
                self._save_manifest_status()

    async def _request_scene(self, text: str) -> dict | str:
        prompt = (
            "把下面的儿童故事口述整理成连续的白板动画分镜 JSON，只输出 JSON，不要 Markdown。"
            "只为本次新增片段返回 1-3 幕 scenes；已经讲过的故事只用于保持角色和场景连续，不能重复生成。"
            "每幕必须有 description 和至少 3 个 elements。"
            "每个元素必须有 kind、x、y、width、height、color、label；"
            "kind 只能是 tree、rabbit、hedgehog、fox、bird、person、house、sun、object、action_lines、simple_shape。"
            "画布是 1280x720；标题栏占用 y=0-100，主体必须放在 y=130-600 的中部区域；"
            "角色在相邻场景中保持相同 kind、颜色和称呼；"
            "不要凭空添加故事里没有的角色或物件，动作要用 action_lines 表现。"
            f"\n角色外观必须连续：{self._character_styles}\n"
            f"已经讲过的故事（仅作连续性参考）：{self._story_so_far}\n本次新增片段（只画这一段）：{text}"
        )
        if self.model is None:
            raise RuntimeError("模型未配置")
        if hasattr(self.model, "generate_scene"):
            result = self.model.generate_scene(prompt)
            return await result if isinstance(result, Awaitable) else result
        messages = [
            {
                "role": "system",
                "content": "你是儿童故事白板动画分镜生成器，只返回符合协议的 JSON，不重复已经生成过的情节。",
            },
            {"role": "user", "content": prompt},
        ]
        chunks = []
        async for event in stream_chat(self.model, messages, []):
            chunks.append(event.text)
        return "".join(chunks)

    async def stop(self) -> dict:
        if self.status in {"idle", "done", "error"}:
            return self.state()
        if self._timer and not self._timer.done():
            self._timer.cancel()
            await asyncio.gather(self._timer, return_exceptions=True)
        # 停止前仍保持 recording，保证最后一段缓冲文本进入任务队列。
        self._flush()
        self.status = "stopping"
        if self._tasks:
            # 故事分镜是一次完整模型请求，给队列留出正常的网络响应时间。
            _, waiting = await asyncio.wait(set(self._tasks), timeout=30)
            for task in waiting:
                task.cancel()
            if waiting:
                await asyncio.gather(*waiting, return_exceptions=True)
        self._save_pending()
        if self.status != "error" and self._error:
            self.status = "error"
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

    def _save_pending(self) -> None:
        for batch_id, text in list(self._pending.items()):
            self._pending.pop(batch_id, None)
            self._error = self._error or "场景生成未完成，未生成伪画面"
            self.status = "error"
            self._publish("error", {"message": self._error, "batchId": batch_id})
        self._save_manifest_status()

    def _save_manifest_status(self) -> None:
        if not self.story_id:
            return
        manifest = self.store.load(self.story_id)["manifest"]
        manifest["status"] = self.status
        manifest["updatedAt"] = datetime.now(UTC).isoformat(timespec="seconds")
        self.store.save_manifest(self.story_id, manifest)

    def state(self) -> dict:
        return {
            "storyId": self.story_id,
            "status": self.status,
            "bufferedText": self._buffer,
            "sceneCount": self._scene_number + len(self._pending),
            "cursor": self._cursor,
        }

    def events(self, since: int = 0) -> list[dict]:
        try:
            cursor = max(0, int(since))
        except (TypeError, ValueError):
            cursor = 0
        return [item for item in self._events if item["id"] > cursor]

    def record_command(self, command: str, text: str = "") -> None:
        """把语音模式命令留在故事事件流，不写入普通会话。"""
        self._publish(
            "command",
            {
                "storyId": self.story_id,
                "command": command,
                "text": str(text or ""),
            },
        )

    def replay(self, story_id: str) -> list[dict]:
        """重新发布已保存场景，供断线后的画布恢复。"""
        data = self.store.load(story_id)
        replayed = {
            event["data"].get("scene", {}).get("sceneId")
            for event in self._events
            if event["type"] == "scene" and event["data"].get("replay")
        }
        for scene in data["scenes"]:
            if scene.get("sceneId") in replayed:
                continue
            payload = {**scene, "svg": scene_to_svg(scene)}
            self._publish("scene", {"storyId": story_id, "scene": payload, "replay": True})
        return data["scenes"]

    def restore(self, story_id: str) -> None:
        """从磁盘恢复故事状态，并把历史场景放入同一事件流。"""
        data = self.store.load(story_id)
        manifest = data["manifest"]
        self.story_id = story_id
        self.session_id = str(manifest.get("sessionId") or "")
        self.status = str(manifest.get("status") or "done")
        # 只有当前内存会话可以继续 recording；磁盘恢复的故事必须让 SSE 收尾。
        if self.status == "recording":
            self.status = "done"
        self._scene_number = max(
            (
                int(match.group(1))
                for scene in data["scenes"]
                if (match := re.fullmatch(r"scene-(\d+)", str(scene.get("sceneId", ""))))
            ),
            default=0,
        )
        if data["scenes"]:
            self._characters = data["scenes"][-1].get("characters", [])
        self.replay(story_id)

    def _publish(self, kind: str, data: dict) -> None:
        self._cursor += 1
        self._events.append({"id": self._cursor, "type": kind, "data": data})

    async def close(self) -> None:
        if self.status == "recording":
            await self.stop()
        elif self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)


def story_command(text: str) -> str | None:
    normalized = "".join(str(text).split())
    if any(
        phrase in normalized for phrase in ("结束讲故事", "停止讲故事", "退出故事模式", "结束故事")
    ):
        return "stop"
    if any(phrase in normalized for phrase in ("开始讲故事", "进入故事模式", "开启故事模式")):
        return "start"
    return None


async def set_story_mode(
    host, enabled: bool, sid: str | None = None, command_text: str = ""
) -> dict:
    sid = host.task_session if sid is None else sid
    if sid not in host.stories:
        store = getattr(host, "story_store", None) or host.story.store
        host.stories[sid] = StorySession(model=host.model, store=store)
    story = host.stories[sid]
    story.session_id = sid
    if enabled:
        if host.meeting.db.active(sid) or host.session_busy(sid):
            raise ValueError("请先结束本对话任务并退出代码修改模式")
        story.start()
        manifest = story.store.load(story.story_id)["manifest"]
        manifest["sessionId"] = sid
        story.store.save_manifest(story.story_id, manifest)
        if command_text:
            story.record_command("start", command_text)
    else:
        if command_text:
            story.record_command("stop", command_text)
        await story.stop()
    if hasattr(host, "recover_stories"):
        host.recover_stories()
    return story.state()


def story_for(host, story_id: str) -> StorySession:
    for story in host.stories.values():
        if story.story_id == story_id:
            return story
    store = getattr(host, "story_store", None) or host.story.store
    story = StorySession(model=host.model, store=store)
    story.restore(story_id)
    # 历史故事不再占用当前会话的 story 槽位，避免串入普通聊天或新故事。
    host.stories[story_id] = story
    return story
