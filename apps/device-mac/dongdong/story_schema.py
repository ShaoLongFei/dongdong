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
    "bear",
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
        (("萤火虫", "点点"), "#FFD34E"),
        (("小熊", "熊", "米米"), "#D9B38C"),
        (("风筝",), "#F2C14E"),
        (("安安", "女孩", "小女孩"), "#F2A65A"),
        (("主人", "爸爸", "妈妈"), "#6A8EBD"),
        (("猫", "小猫"), "#B7B7B7"),
        (("雨伞", "伞"), "#F2C14E"),
        (("气球",), "#D94B4B"),
        (("彩虹",), "#ECA6A6"),
        (("石板路", "小路"), "#A9A9A9"),
        (("公园",), "#6B8E68"),
        (("兔子", "小兔"), "#E8D7C5"),
        (("种子", "月亮种子"), "#D9B7FF"),
        (("发光的花", "小花"), "#F6D84A"),
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
    if any(word in label for word in ("小熊", "熊", "米米")):
        return "bear"
    if "刺猬" in label or "团团" in label:
        return "hedgehog"
    return raw if raw in ALLOWED_KINDS else "simple_shape"


def _scene_title(text: str) -> str:
    text = str(text or "").strip()
    if "小猫" in text and "气球" in text and "彩虹" in text:
        return "雨后帮小猫找气球"
    if "小猫" in text and "主人" in text:
        return "小猫找回家"
    if "小猫" in text and ("发现" in text or "遇见" in text):
        return "公园门口遇见小猫"
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
    if kind in {"fox", "rabbit", "hedgehog", "person", "bear"}:
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
    has_water = any(
        item["kind"] in {"object", "simple_shape"}
        and any(word in item["label"] for word in ("溪", "水"))
        for item in elements
    )
    boat_scene = any(
        any(word in item["label"] for word in ("小船", "船", "划行", "捞回")) for item in elements
    )
    celebration = any(word in text for word in ("庆祝", "欢呼", "朋友都来", "朋友们都来"))
    left, right = (
        (170, 760)
        if trapped
        else (120, 780)
        if rescue
        else (220, 760)
        if gift
        else (280, 700)
        if promise
        else (220, 760)
    )
    if boat_scene:
        # 角色要坐进叶船，船和溪水必须共享同一块画面；之前分别套岸边槽位，
        # 导致“人在岸上、船在水外”的断裂构图。
        left, right = 390, 650
    elif has_water and not trapped and not rescue:
        # 水面固定在画面右下，角色放在岸边，避免脚和溪流重叠。
        left, right = 170, 540
    bird_x, bird_y = (510, 245) if rescue else (520, 270)
    slots = {
        "fox": (left, 390 if promise else 350),
        "rabbit": (left, 390 if promise else 350),
        "person": (left, 390 if promise else 350),
        "bear": (left, 390 if promise else 350),
        "hedgehog": (right, 390 if promise else 350),
        "bird": (bird_x, bird_y),
    }
    used = set()
    # 模型偶尔在后续镜头漏掉已出现角色；文本仍明确提及时补回，保证连续性。
    known_characters = (
        ("bear", "小熊米米", ("小熊", "米米")),
        ("bird", "小鸟圆圆", ("小鸟", "圆圆")),
        ("fox", "狐狸阿栗", ("狐狸", "阿栗")),
        ("rabbit", "小兔子", ("兔子",)),
        ("hedgehog", "刺猬团团", ("刺猬", "团团")),
    )
    known_kinds = {item["kind"] for item in elements}
    for kind, label, words in known_characters:
        if any(word in text for word in words) and kind not in known_kinds:
            elements.append(
                _element(
                    {
                        "kind": kind,
                        "label": label,
                        "width": 210 if kind == "bear" else 170,
                        "height": 230 if kind == "bear" else 145,
                    },
                    len(elements),
                    0,
                )
            )
            known_kinds.add(kind)
    if celebration:
        # “森林里的朋友都来庆祝”是故事明确写出的群体角色；模型偶尔只返回主角，
        # 补两只小鸟让结尾有可见的庆祝参与者，同时不改变主角连续性。
        friend_count = sum(
            1 for item in elements if item["kind"] == "bird" and "庆祝" in item["label"]
        )
        for index in range(friend_count, 2):
            elements.append(
                _element(
                    {
                        "kind": "bird",
                        "label": f"庆祝的朋友{index + 1}",
                        "x": 1010 + index * 100,
                        "y": 250 + index * 35,
                        "width": 130,
                        "height": 110,
                        "color": "#E5A83B" if index == 0 else "#C94F5C",
                    },
                    len(elements),
                    0,
                )
            )

    for item in elements:
        if item["kind"] not in slots or item["kind"] in used:
            continue
        x, y = slots[item["kind"]]
        item.update(x=x, y=y)
        used.add(item["kind"])

    # 不同种类的角色默认可能拿到同一个左侧槽位；按画面中的角色数量重新排开，
    # 避免“遇见”类镜头把两个角色画成一个重叠的人形。
    characters = [
        item for item in elements if item["kind"] in {"fox", "rabbit", "hedgehog", "person"}
    ]
    if len(characters) > 1:
        y = 390 if promise else 320 if boat_scene else 350
        if len(characters) == 2:
            positions = [(left, y), (right, y)]
        else:
            step = max(180, (right - left) // (len(characters) - 1))
            positions = [(min(right, left + index * step), y) for index in range(len(characters))]
        for item, (x, item_y) in zip(characters, positions):
            item.update(x=x, y=item_y)

    for item in elements:
        label = item["label"]
        if (
            item["kind"] in {"object", "simple_shape"}
            and "种子" in label
            and not any(word in label for word in ("空出", "原来", "不见", "没有"))
        ):
            item.update(
                x=(620 if boat_scene else 560) if "对岸" not in label else 1030,
                y=480 if boat_scene else 390,
                width=max(64, min(item["width"], 120)),
                height=max(64, min(item["height"], 100)),
            )
        elif item["kind"] in {"object", "simple_shape"} and any(
            word in label for word in ("溪", "水")
        ):
            if boat_scene:
                item.update(
                    x=260,
                    y=455,
                    width=max(900, min(item["width"], 980)),
                    height=max(150, item["height"]),
                )
            else:
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
        elif item["kind"] == "object" and any(word in label for word in ("猫", "小猫")):
            item.update(
                x=520,
                y=405,
                width=max(90, min(item["width"], 150)),
                height=max(90, min(item["height"], 110)),
            )
        elif item["kind"] == "object" and any(word in label for word in ("雨伞", "伞")):
            item.update(
                x=350, y=190, width=max(item["width"], 240), height=max(item["height"], 125)
            )
        elif item["kind"] == "object" and "气球" in label:
            if any(word in text for word in ("小桥下", "桥下", "递给")):
                item.update(
                    x=610, y=300, width=max(item["width"], 75), height=max(item["height"], 95)
                )
            elif any(word in text for word in ("天空", "彩虹", "告别")):
                item.update(
                    x=585, y=300, width=max(item["width"], 75), height=max(item["height"], 95)
                )
            else:
                item.update(
                    x=900, y=180, width=max(item["width"], 75), height=max(item["height"], 95)
                )
        elif item["kind"] in {"object", "simple_shape"} and "彩虹" in label:
            item.update(
                x=850, y=165, width=max(item["width"], 300), height=max(item["height"], 130)
            )
        elif item["kind"] in {"object", "simple_shape"} and "石板路" in label:
            item.update(x=190, y=500, width=max(item["width"], 850), height=max(item["height"], 55))
        elif item["kind"] in {"object", "simple_shape"} and "公园门口" in label:
            item.update(x=70, y=180, width=max(item["width"], 390), height=max(item["height"], 240))
        elif item["kind"] in {"object", "simple_shape"} and "桥" in label:
            item.update(
                x=580, y=430, width=max(item["width"], 360), height=max(item["height"], 100)
            )
        elif item["kind"] == "object" and "风筝" in label:
            if any(word in label for word in ("天空", "飞")) or any(
                word in text for word in ("放上天空", "重新放", "升空", "放飞")
            ):
                item.update(
                    x=700, y=180, width=max(item["width"], 125), height=max(item["height"], 85)
                )
            elif any(word in label for word in ("桥另一边", "对岸")):
                item.update(
                    x=930, y=300, width=max(item["width"], 125), height=max(item["height"], 85)
                )
            elif any(word in text for word in ("树枝", "挂", "大树")):
                item.update(
                    x=895, y=300, width=max(item["width"], 125), height=max(item["height"], 85)
                )
            else:
                item.update(
                    x=520, y=470, width=max(item["width"], 150), height=max(item["height"], 85)
                )
        elif item["kind"] == "object" and any(word in label for word in ("小船", "船", "树叶")):
            item.update(
                x=350 if boat_scene else 420,
                y=420 if boat_scene else 500,
                width=max(item["width"], 600 if boat_scene else 230),
                height=max(item["height"], 190 if boat_scene else 65),
            )
        elif item["kind"] in {"object", "simple_shape"} and any(
            word in label for word in ("山谷", "草坡", "草地", "山坡")
        ):
            item.update(x=0, y=455, width=1280, height=max(item["height"], 180))
        elif "蒲公英" in label:
            item.update(x=item["x"], y=455)
        elif item["kind"] == "action_lines":
            if boat_scene:
                item.update(
                    x=430, y=125, width=max(item["width"], 360), height=max(item["height"], 100)
                )
            elif any(word in label for word in ("剪断", "取下", "修理", "风筝", "欢呼")):
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
            elif any(word in label for word in ("递", "挥", "雨滴", "气球")):
                item.update(
                    x=430, y=235, width=max(item["width"], 360), height=max(item["height"], 180)
                )
            else:
                item.update(
                    x=500, y=225, width=max(item["width"], 260), height=max(item["height"], 130)
                )
    if promise:
        for item in elements:
            if item["kind"] in {"fox", "hedgehog", "rabbit", "person", "bear"}:
                item["pose"] = "sitting"
    elif trapped:
        for item in elements:
            if item["kind"] == "hedgehog":
                item["pose"] = "sitting"
    if boat_scene:
        for item in elements:
            if item["kind"] in {"fox", "rabbit", "hedgehog", "person", "bear"}:
                item["pose"] = "sitting"
        for index, item in enumerate(
            item for item in elements if item["kind"] == "bird" and "庆祝" in item["label"]
        ):
            item.update(x=(430, 1080)[index % 2], y=190 + index * 35)
    if celebration:
        friends = [item for item in elements if item["kind"] == "bird" and "庆祝" in item["label"]]
        for index, item in enumerate(friends):
            item.update(x=(430, 1080)[index % 2], y=190 + index * 35)
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
                        "小猫",
                        "气球",
                        "彩虹",
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
