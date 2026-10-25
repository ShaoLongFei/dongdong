"""受控故事场景协议。"""

from __future__ import annotations

import json
import re

WIDTH, HEIGHT = 1280, 720
BACKGROUND = "#F5EBD7"
ALLOWED_COLORS = {
    BACKGROUND,
    "#5B5550",
    "#C94C4C",
    "#E88A3D",
    "#4C78A8",
    "#6B4226",
    "#D9B38C",
    "#B8A080",
    "#77BBDD",
    "#806040",
    "#6A9CC9",
    "#FFD45C",
    "#4F8B45",
    "#9B6B3D",
    "#E5A83B",
    "#F28C45",
    "#C94F5C",
}
ALLOWED_KINDS = {
    "tree",
    "rabbit",
    "hedgehog",
    "fox",
    "bird",
    "person",
    "house",
    "sun",
    "object",
    "action_lines",
    "simple_shape",
}
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _color(value: object, default: str = "#5B5550") -> str:
    value = str(value or "")
    return value if value in ALLOWED_COLORS or _HEX_COLOR.fullmatch(value) else default


def _visual_color(item: dict) -> str:
    label = str(item.get("label") or "")
    for words, color in (
        (("溪", "水"), "#78BFE0"),
        (("桥", "木板"), "#8B5A2B"),
        (("藤", "树枝"), "#4F8B45"),
        (("果", "莓"), "#D93636"),
        (("草坡", "草地"), "#78B84A"),
        (("山谷", "山"), "#B7D7A8"),
        (("蒲公英",), "#F6D84A"),
        (("雨", "乌云"), "#8FA8B8"),
        (("太阳", "夕阳"), "#F2B84B"),
    ):
        if any(word in label for word in words):
            return color
    return _color(item.get("color"))


def _kind(item: dict) -> str:
    raw = item.get("kind")
    if raw in ALLOWED_KINDS and raw not in {"person", "object", "simple_shape"}:
        return raw
    label = str(item.get("label") or "")
    if any(word in label for word in ("狐狸", "阿栗")):
        return "fox"
    if "啄木鸟" in label:
        return "bird"
    if "刺猬" in label or "团团" in label:
        return "hedgehog"
    return raw if raw in ALLOWED_KINDS else "simple_shape"


def _scene_title(text: str) -> str:
    text = str(text or "").strip()
    if ("缠住" in text or "被困" in text) and ("发现" in text or "哭声" in text):
        return "发现被困的团团"
    if "啄木鸟" in text and ("剪断" in text or "帮忙" in text):
        return "啄木鸟来帮忙"
    if ("红果子" in text or "果子" in text) and ("获救" in text or "断开" in text):
        return "红果子和感谢"
    if ("蒲公英" in text or "草坡" in text) and ("约定" in text or "办法" in text):
        return "蒲公英草坡的约定"
    if "哭声" in text and ("寻找" in text or "听见" in text):
        return "溪边传来的哭声"
    return text[:14] or "故事这一幕"


def _safe_int(value: object, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def _element(raw: object, index: int, start_ms: int) -> dict:
    item = raw if isinstance(raw, dict) else {}
    kind = _kind(item)
    color = _visual_color(item)
    width = _safe_int(item.get("width"), 180, 1, WIDTH)
    height = _safe_int(item.get("height"), 160, 1, HEIGHT)
    if kind in {"fox", "rabbit", "hedgehog", "person"}:
        width, height = max(width, 210), max(height, 230)
    elif kind == "bird":
        width, height = max(width, 170), max(height, 145)
    x = _safe_int(item.get("x"), 40 + (index * 150) % (WIDTH - width + 1), 0, WIDTH - width)
    y = _safe_int(item.get("y"), 80 + (index * 70) % (HEIGHT - height + 1), 0, HEIGHT - height)
    duration = _safe_int(item.get("durationMs"), 900, 100, 12_000)
    label = str(item.get("label") or "")[:120]
    if kind in {"fox", "rabbit", "hedgehog", "bird", "person"}:
        y = max(180, min(420, y))
    elif kind == "action_lines":
        y = max(130, min(450, y))
    elif width < 700:
        y = max(120, min(500, y))
    margin = 120 if kind in {"fox", "rabbit", "hedgehog", "bird", "person"} else 36
    x = max(margin, min(WIDTH - width - margin, x))
    pose = str(item.get("pose") or "standing")
    if pose not in {"standing", "sitting"}:
        pose = "standing"
    return {
        "id": str(item.get("id") or f"element-{index + 1}")[:64],
        "kind": kind,
        "label": label,
        "narrativeRole": str(item.get("narrativeRole") or "")[:80],
        "pose": pose,
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "color": color,
        "action": "draw",
        "startMs": start_ms,
        "durationMs": duration,
    }


def _compose(elements: list[dict], story_text: str) -> list[dict]:
    """把模型元素放入一组有叙事关系的镜头槽位。"""
    text = str(story_text or "")
    trapped = "缠" in text or "被困" in text
    rescue = "剪断" in text or "帮忙" in text
    gift = "果子" in text or "获救" in text
    promise = "约定" in text or "办法" in text
    if not rescue and not any(word in text for word in ("啄木鸟", "鸟")):
        elements = [item for item in elements if item["kind"] != "bird"]
    left, right = (
        (170, 760)
        if trapped
        else (120, 780)
        if rescue
        else (220, 760)
        if gift
        else (280, 700)
        if promise
        else (420, 420)
    )
    bird_x, bird_y = (510, 245) if rescue else (520, 270)
    slots = {
        "fox": (left, 390 if promise else 350),
        "rabbit": (left, 390 if promise else 350),
        "person": (left, 390 if promise else 350),
        "hedgehog": (right, 390 if promise else 350),
        "bird": (bird_x, bird_y),
    }
    used = set()
    for item in elements:
        if item["kind"] not in slots or item["kind"] in used:
            continue
        x, y = slots[item["kind"]]
        item.update(x=x, y=y)
        used.add(item["kind"])

    for item in elements:
        label = item["label"]
        if any(word in label for word in ("溪", "水")):
            item.update(
                x=820,
                y=500,
                width=max(360, min(item["width"], 460)),
                height=max(64, item["height"]),
            )
        elif item["kind"] == "object" and any(word in label for word in ("藤", "树枝")):
            item.update(
                x=610 if rescue else 700,
                y=520,
                width=max(item["width"], 300),
                height=max(item["height"], 54),
            )
        elif item["kind"] == "object" and "果" in label:
            item.update(x=535, y=390, width=max(item["width"], 64), height=max(item["height"], 64))
        elif any(word in label for word in ("山谷", "草坡", "草地")):
            item.update(
                x=60, y=455, width=max(item["width"], 1120), height=max(item["height"], 180)
            )
        elif "蒲公英" in label:
            item.update(x=item["x"], y=455)
        elif item["kind"] == "action_lines":
            if "剪断" in label:
                item.update(
                    x=560, y=235, width=max(item["width"], 300), height=max(item["height"], 180)
                )
            elif "果子" in label:
                item.update(
                    x=330, y=265, width=max(item["width"], 460), height=max(item["height"], 150)
                )
            elif "约定" in label:
                item.update(
                    x=460, y=275, width=max(item["width"], 300), height=max(item["height"], 150)
                )
            else:
                item.update(
                    x=500, y=225, width=max(item["width"], 260), height=max(item["height"], 130)
                )
    if promise:
        for item in elements:
            if item["kind"] in {"fox", "hedgehog", "rabbit", "person"}:
                item["pose"] = "sitting"
    elif trapped:
        for item in elements:
            if item["kind"] == "hedgehog":
                item["pose"] = "sitting"
    for item in elements:
        item["x"] = max(36, min(WIDTH - item["width"] - 36, item["x"]))
    return elements


def default_scene(story_text: str, scene_id: str) -> dict:
    text = str(story_text or "").strip()[:500]
    elements = [
        _element({"kind": "simple_shape", "x": 80, "y": 470, "width": 1120, "height": 100}, 0, 0),
        _element({"kind": "sun", "x": 980, "y": 80, "width": 130, "height": 130}, 1, 700),
        _element({"kind": "object", "x": 510, "y": 280, "width": 220, "height": 180}, 2, 1400),
    ]
    return {
        "sceneId": scene_id if _SAFE_ID.fullmatch(scene_id or "") else "scene-001",
        "title": _scene_title(text),
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
                if not isinstance(item, dict) or _kind(item) not in ALLOWED_KINDS:
                    raise ValueError(f"模型场景第 {index} 个元素缺少合法 kind")
        elements = _compose(
            [_element(item, index, 0) for index, item in enumerate(raw_elements[:8])],
            story_text,
        )
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
                        "kind": _kind(item),
                        "color": _color(item.get("color")),
                    }
                )
        duration = max(cursor, _safe_int(data.get("durationMs"), cursor, 100, 30_000))
        return {
            "sceneId": scene_id if _SAFE_ID.fullmatch(scene_id or "") else "scene-001",
            "title": (
                _scene_title(story_text)
                if any(
                    word in story_text
                    for word in (
                        "哭声",
                        "缠住",
                        "被困",
                        "啄木鸟",
                        "剪断",
                        "果子",
                        "获救",
                        "草坡",
                        "蒲公英",
                        "约定",
                    )
                )
                else str(data.get("title") or _scene_title(story_text)).strip()[:80]
            ),
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


def normalize_scenes(raw: dict | str, story_text: str, scene_number: int) -> list[dict]:
    """保留模型返回的连续分镜，而不是只取 scenes[0]。"""
    data = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(data, dict):
        raise ValueError("scenes must be an object")
    raw_scenes = data.get("scenes")
    if not isinstance(raw_scenes, list):
        raw_scenes = [data]
    scenes = []
    for index, item in enumerate(raw_scenes[:4]):
        if not isinstance(item, dict):
            raise ValueError(f"第 {index + 1} 幕不是对象")
        scene_text = str(item.get("description") or story_text).strip()[:500]
        scene_id = f"scene-{scene_number + index:03d}"
        scenes.append(normalize_scene(item, scene_text, scene_id, strict=True))
    if not scenes:
        raise ValueError("模型没有返回任何场景")
    return scenes
