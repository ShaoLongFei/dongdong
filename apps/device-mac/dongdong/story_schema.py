"""受控故事场景协议。"""

from __future__ import annotations

import json
import re

WIDTH, HEIGHT = 1280, 720
BACKGROUND = "#F5EBD7"
ALLOWED_COLORS = {"#5B5550", "#C94C4C", "#E88A3D", "#4C78A8", BACKGROUND}
ALLOWED_KINDS = {
    "tree",
    "rabbit",
    "person",
    "house",
    "sun",
    "object",
    "action_lines",
    "simple_shape",
}
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def _safe_int(value: object, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def _element(raw: object, index: int, start_ms: int) -> dict:
    item = raw if isinstance(raw, dict) else {}
    kind = item.get("kind") if item.get("kind") in ALLOWED_KINDS else "simple_shape"
    color = item.get("color") if item.get("color") in ALLOWED_COLORS else "#5B5550"
    width = _safe_int(item.get("width"), 180, 1, WIDTH)
    height = _safe_int(item.get("height"), 160, 1, HEIGHT)
    x = _safe_int(item.get("x"), 40 + (index * 150) % (WIDTH - width + 1), 0, WIDTH - width)
    y = _safe_int(item.get("y"), 80 + (index * 70) % (HEIGHT - height + 1), 0, HEIGHT - height)
    duration = _safe_int(item.get("durationMs"), 900, 100, 12_000)
    label = str(item.get("label") or "")[:120]
    return {
        "id": str(item.get("id") or f"element-{index + 1}")[:64],
        "kind": kind,
        "label": label,
        "narrativeRole": str(item.get("narrativeRole") or "")[:80],
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "color": color,
        "action": "draw",
        "startMs": start_ms,
        "durationMs": duration,
    }


def default_scene(story_text: str, scene_id: str) -> dict:
    text = str(story_text or "").strip()[:500]
    elements = [
        _element({"kind": "simple_shape", "x": 80, "y": 470, "width": 1120, "height": 100}, 0, 0),
        _element({"kind": "sun", "x": 980, "y": 80, "width": 130, "height": 130}, 1, 700),
        _element({"kind": "object", "x": 510, "y": 280, "width": 220, "height": 180}, 2, 1400),
    ]
    return {
        "sceneId": scene_id if _SAFE_ID.fullmatch(scene_id or "") else "scene-001",
        "storyText": text,
        "storyBasis": text[:200],
        "durationMs": 3_500,
        "characters": [],
        "elements": elements,
    }


def normalize_scene(
    raw: dict | str, story_text: str, scene_id: str, *, strict: bool = False
) -> dict:
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(data, dict):
            raise ValueError("scene must be an object")
        # 部分模型会额外包一层 scenes；取第一幕后仍按同一受控协议校验。
        if "elements" not in data and isinstance(data.get("scenes"), list):
            first = data["scenes"][0] if data["scenes"] else None
            if isinstance(first, dict):
                data = first
        raw_elements = data.get("elements")
        if not isinstance(raw_elements, list):
            raise ValueError("elements must be a list")
        if strict and len(raw_elements) < 3:
            raise ValueError("模型场景至少需要 3 个元素")
        if strict:
            for index, item in enumerate(raw_elements[:8], 1):
                if not isinstance(item, dict) or item.get("kind") not in ALLOWED_KINDS:
                    raise ValueError(f"模型场景第 {index} 个元素缺少合法 kind")
        elements = [_element(item, index, 0) for index, item in enumerate(raw_elements[:8])]
        if len(elements) < 3:
            raise ValueError("scene requires at least three elements")
        cursor = 0
        for element in elements:
            element["startMs"] = cursor
            cursor += element["durationMs"]
        characters = []
        for item in data.get("characters", []) if isinstance(data.get("characters"), list) else []:
            if isinstance(item, dict):
                characters.append(
                    {
                        "id": str(item.get("id") or "character")[:64],
                        "kind": item.get("kind") if item.get("kind") in ALLOWED_KINDS else "person",
                        "color": item.get("color")
                        if item.get("color") in ALLOWED_COLORS
                        else "#5B5550",
                    }
                )
        duration = max(cursor, _safe_int(data.get("durationMs"), cursor, 100, 30_000))
        return {
            "sceneId": scene_id if _SAFE_ID.fullmatch(scene_id or "") else "scene-001",
            "storyText": str(story_text or "").strip()[:500],
            "storyBasis": str(data.get("storyBasis") or story_text or "").strip()[:500],
            "durationMs": duration,
            "characters": characters[:8],
            "elements": elements,
        }
    except (TypeError, ValueError, json.JSONDecodeError):
        if strict:
            raise
        return default_scene(story_text, scene_id)
