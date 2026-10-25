"""把受控故事元素绘制为无外部依赖的 SVG。"""

from __future__ import annotations

from html import escape

from .story_schema import ALLOWED_KINDS, BACKGROUND, HEIGHT, WIDTH, _color


def _number(value: object, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        number = default
    return max(low, min(high, number))


def _safe_element(raw: object, index: int) -> dict:
    item = raw if isinstance(raw, dict) else {}
    width = _number(item.get("width"), 180, 1, WIDTH)
    height = _number(item.get("height"), 160, 1, HEIGHT)
    kind = item.get("kind")
    color = item.get("color")
    return {
        "kind": kind if isinstance(kind, str) and kind in ALLOWED_KINDS else "simple_shape",
        "color": _color(color),
        "label": str(item.get("label") or "")[:120],
        "pose": item.get("pose") if item.get("pose") in {"standing", "sitting"} else "standing",
        "x": _number(item.get("x"), 40 + (index * 150) % (WIDTH - width + 1), 0, WIDTH - width),
        "y": _number(item.get("y"), 80 + (index * 70) % (HEIGHT - height + 1), 0, HEIGHT - height),
        "width": width,
        "height": height,
        "startMs": _number(item.get("startMs"), 0, 0, 30_000),
        "durationMs": _number(item.get("durationMs"), 900, 100, 30_000),
    }


def _shape(element: dict) -> str:
    x, y = element["x"], element["y"]
    w, h = element["width"], element["height"]
    c = escape(element["color"], quote=True)
    kind = element["kind"]
    label = element["label"]
    if "溪" in label or "水" in label:
        return (
            f'<path d="M{x},{y + h // 2} q{w // 8},-{h // 2} {w // 4},0 t{w // 4},0 t{w // 4},0 t{w // 4},0 V{y + h} H{x} Z" fill="{c}" opacity=".9"/>'
            f'<path d="M{x + 20},{y + h * 2 // 3} q{w // 8},-{h // 3} {w // 4},0 t{w // 4},0 t{w // 4},0" fill="none" stroke="#E9F3F5" stroke-width="5" opacity=".8"/>'
        )
    if "草坡" in label or "草地" in label:
        return f'<path d="M{x},{y + h} Q{x + w // 4},{y + h // 2} {x + w // 2},{y + h * 3 // 4} T{x + w},{y + h // 2} V720 H{x} Z" fill="{c}"/>'
    if "蒲公英" in label:
        return f'<path d="M{x + w // 2},{y + h} V{y + h // 2}" stroke="#4F8B45" stroke-width="5"/><circle cx="{x + w // 2}" cy="{y + h // 3}" r="{max(8, min(w, h) // 4)}" fill="{c}"/>'
    if "山谷" in label or label == "山":
        return f'<path d="M{x},{y + h} L{x + w // 3},{y + h // 3} L{x + w // 2},{y + h * 2 // 3} L{x + w * 3 // 4},{y + h // 5} L{x + w},{y + h} Z" fill="{c}"/>'
    if kind == "tree":
        return f'<rect x="{x + w // 2 - 16}" y="{y + h // 2}" width="32" height="{h // 2}" fill="{c}"/><circle cx="{x + w // 2}" cy="{y + h // 3}" r="{min(w, h) // 3}" fill="{c}"/>'
    if kind == "rabbit":
        return f'<ellipse cx="{x + w // 2}" cy="{y + h // 2}" rx="{w // 3}" ry="{h // 3}" fill="{c}"/><circle cx="{x + w // 2}" cy="{y + h // 4}" r="{min(w, h) // 5}" fill="{c}"/><path d="M{x + w // 2 - 22},{y + h // 5} l-12,-{h // 4} l28,18 M{x + w // 2 + 22},{y + h // 5} l12,-{h // 4} l-28,18" fill="none" stroke="{c}" stroke-width="10"/>'
    if kind == "fox":
        cx, head_y = x + w // 2, y + h // 3
        if element.get("pose") == "sitting":
            return (
                f'<path d="M{x + w // 8},{y + h * 4 // 5} Q{x - 8},{y + h * 2 // 3} {x + w // 4},{y + h * 3 // 5} '
                f'Q{x + w // 3},{y + h * 2 // 3} {x + w * 2 // 5},{y + h * 3 // 4}" fill="none" stroke="{c}" stroke-width="{max(16, w // 7)}" stroke-linecap="round"/>'
                f'<ellipse cx="{cx}" cy="{y + h * 3 // 5}" rx="{w // 3}" ry="{h // 3}" fill="{c}" stroke="#5B5550" stroke-width="4"/>'
                f'<path d="M{cx - w // 5},{head_y - h // 5} L{cx - w // 3},{y + 8} L{cx - w // 14},{head_y - h // 8} M{cx + w // 5},{head_y - h // 5} L{cx + w // 3},{y + 8} L{cx + w // 14},{head_y - h // 8}" fill="{c}" stroke="#5B5550" stroke-width="4" stroke-linejoin="round"/>'
                f'<circle cx="{cx}" cy="{head_y}" r="{min(w, h) // 4}" fill="{c}" stroke="#5B5550" stroke-width="4"/>'
                f'<path d="M{cx - w // 7},{head_y + h // 18} Q{cx},{head_y + h // 7} {cx + w // 7},{head_y + h // 18}" fill="#F5EBD7" stroke="#5B5550" stroke-width="3"/>'
                f'<circle cx="{cx - w // 10}" cy="{head_y - 4}" r="6" fill="#3D382F"/><circle cx="{cx + w // 10}" cy="{head_y - 4}" r="6" fill="#3D382F"/>'
                f'<path d="M{cx - w // 6},{y + h * 4 // 5} h{w // 7} M{cx + w // 12},{y + h * 4 // 5} h{w // 7}" stroke="#5B5550" stroke-width="10" stroke-linecap="round"/>'
            )
        return (
            f'<path d="M{x + w // 4},{y + h * 3 // 5} Q{x + 2},{y + h * 2 // 3} {x + w // 8},{y + h * 4 // 5} '
            f'Q{x + w // 4},{y + h * 4 // 5} {x + w * 2 // 5},{y + h * 2 // 3}" fill="none" '
            f'stroke="{c}" stroke-width="{max(12, w // 7)}" stroke-linecap="round"/>'
            f'<ellipse cx="{cx}" cy="{y + h * 3 // 5}" rx="{w // 3}" ry="{h // 4}" fill="{c}" stroke="#5B5550" stroke-width="4"/>'
            f'<path d="M{cx - w // 5},{head_y - h // 5} L{cx - w // 3},{y + 8} L{cx - w // 14},{head_y - h // 8} '
            f'M{cx + w // 5},{head_y - h // 5} L{cx + w // 3},{y + 8} L{cx + w // 14},{head_y - h // 8}" '
            f'fill="{c}" stroke="#5B5550" stroke-width="4" stroke-linejoin="round"/>'
            f'<circle cx="{cx}" cy="{head_y}" r="{min(w, h) // 4}" fill="{c}" stroke="#5B5550" stroke-width="4"/>'
            f'<path d="M{cx - w // 7},{head_y + h // 18} Q{cx},{head_y + h // 7} {cx + w // 7},{head_y + h // 18}" '
            f'fill="#F5EBD7" stroke="#5B5550" stroke-width="3"/>'
            f'<circle cx="{cx - w // 10}" cy="{head_y - 4}" r="5" fill="#3D382F"/><circle cx="{cx + w // 10}" cy="{head_y - 4}" r="5" fill="#3D382F"/>'
            f'<path d="M{cx - 6},{head_y + h // 12} l6,5 l6,-5" fill="none" stroke="#3D382F" stroke-width="3"/>'
            f'<path d="M{cx - w // 6},{y + h * 3 // 4} v{h // 5} M{cx + w // 6},{y + h * 3 // 4} v{h // 5}" stroke="#5B5550" stroke-width="8" stroke-linecap="round"/>'
        )
    if kind == "hedgehog":
        cx, cy = x + w // 2, y + h // 2
        body_rx, body_ry = max(34, w // 2 - 12), max(34, h // 3)
        spikes = " ".join(
            f"M{x + w * n // 10},{y + h // 2} l{-14 if n % 2 else 14},{-h // 5}"
            for n in range(2, 9)
        )
        snout_x = x + w - 34
        sitting = element.get("pose") == "sitting"
        body_y = y + h * 3 // 5 if sitting else cy + 10
        return (
            f'<path d="{spikes}" fill="none" stroke="#5B5550" stroke-width="7" stroke-linecap="round"/>'
            f'<ellipse cx="{cx - 4}" cy="{body_y}" rx="{body_rx}" ry="{body_ry + (10 if sitting else 0)}" fill="{c}" stroke="#5B5550" stroke-width="4"/>'
            f'<ellipse cx="{snout_x - 18}" cy="{cy - 2}" rx="{max(22, w // 5)}" ry="{max(25, h // 5)}" fill="#D9B38C" stroke="#5B5550" stroke-width="3"/>'
            f'<circle cx="{snout_x - 18}" cy="{cy - 8}" r="6" fill="#3D382F"/><circle cx="{snout_x + 2}" cy="{cy + 4}" r="7" fill="#3D382F"/>'
            f'<path d="M{x + w // 5},{y + h * 4 // 5} h{w // 8} M{x + w * 3 // 5},{y + h * 4 // 5} h{w // 8}" stroke="#5B5550" stroke-width="9" stroke-linecap="round"/>'
        )
    if kind == "bird":
        return (
            f'<path d="M{x + w // 4},{y + h * 2 // 3} l-{w // 4},{h // 5} l{w // 5},-{h // 4}" fill="{c}" stroke="#5B5550" stroke-width="4"/>'
            f'<ellipse cx="{x + w // 2}" cy="{y + h // 2}" rx="{w // 3}" ry="{h // 3}" fill="{c}" stroke="#5B5550" stroke-width="4"/>'
            f'<circle cx="{x + w * 2 // 3}" cy="{y + h // 3}" r="{min(w, h) // 5}" fill="{c}" stroke="#5B5550" stroke-width="4"/>'
            f'<path d="M{x + w * 2 // 3 + 15},{y + h // 3} l28,10 l-28,10 Z" fill="#E5A83B" stroke="#5B5550" stroke-width="3"/>'
            f'<path d="M{x + w // 2},{y + h // 2} q-{w // 4},-{h // 6} -{w // 3},0" fill="none" stroke="#5B5550" stroke-width="5"/>'
            f'<circle cx="{x + w * 2 // 3}" cy="{y + h // 3 - 4}" r="4" fill="#3D382F"/>'
        )
    if kind == "person":
        return f'<circle cx="{x + w // 2}" cy="{y + h // 4}" r="{min(w, h) // 7}" fill="{c}"/><path d="M{x + w // 2},{y + h // 3} v{h // 3} m-{w // 5},-{h // 6} h{w // 2} m-{w // 8},{h // 3} l-{w // 6},{h // 4} m{w // 8},{h // 4} l{w // 6},{h // 4}" fill="none" stroke="{c}" stroke-width="12" stroke-linecap="round"/>'
    if kind == "house":
        return f'<path d="M{x},{y + h // 3} L{x + w // 2},{y} L{x + w},{y + h // 3} V{y + h} H{x} Z" fill="{c}"/><rect x="{x + w // 3}" y="{y + h // 2}" width="{w // 3}" height="{h // 2}" fill="{BACKGROUND}"/>'
    if kind == "sun":
        return f'<circle cx="{x + w // 2}" cy="{y + h // 2}" r="{min(w, h) // 3}" fill="{c}"/><circle cx="{x + w // 2}" cy="{y + h // 2}" r="{min(w, h) // 2 - 8}" fill="none" stroke="{c}" stroke-width="6" stroke-dasharray="12 14"/>'
    if kind == "action_lines":
        if any(word in label for word in ("哭声", "缠住脚", "剪断", "果子", "约定")):
            return ""
        return (
            f'<path d="M{x + 10},{y + h * 3 // 5} q{w // 5},-{h // 3} {w * 2 // 5},0 '
            f'M{x + w // 4},{y + h * 4 // 5} q{w // 5},-{h // 3} {w * 2 // 5},0" fill="none" '
            f'stroke="{c}" stroke-width="7" stroke-linecap="round"/>'
            f'<path d="M{x + w * 3 // 5},{y + h // 3} l22,12 l-20,12" fill="none" stroke="{c}" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/>'
        )
    if kind == "object":
        if "溪" in label or "水" in label:
            return f'<path d="M{x},{y + h // 2} q{w // 8},-{h // 2} {w // 4},0 t{w // 4},0 t{w // 4},0 t{w // 4},0 V{y + h} H{x} Z" fill="{c}" opacity=".9"/><path d="M{x + 20},{y + h * 2 // 3} q{w // 8},-{h // 3} {w // 4},0 t{w // 4},0 t{w // 4},0" fill="none" stroke="#E9F3F5" stroke-width="5" opacity=".8"/>'
        if "桥" in label:
            return f'<rect x="{x}" y="{y + h // 3}" width="{w}" height="{h // 3}" rx="8" fill="{c}"/><path d="M{x + 15},{y + h // 3} V{y + h} M{x + w - 15},{y + h // 3} V{y + h}" stroke="#5B5550" stroke-width="8"/>'
        if "果" in label:
            return f'<circle cx="{x + w // 2}" cy="{y + h // 2 + 8}" r="{min(w, h) // 3}" fill="{c}"/><path d="M{x + w // 2},{y + h // 3} q12,-22 30,-8" fill="none" stroke="#4F8B45" stroke-width="8"/>'
        if "藤" in label or "树枝" in label:
            return (
                f'<path d="M{x},{y + h // 2} q{w // 4},-{h} {w // 2},0 t{w // 2},0" fill="none" stroke="{c}" stroke-width="{max(8, h // 2)}" stroke-linecap="round"/>'
                f'<path d="M{x + w // 3},{y + h // 2 - 12} l18,-18 M{x + w * 2 // 3},{y + h // 2 + 12} l18,18" stroke="#2F6D35" stroke-width="5" stroke-linecap="round"/>'
            )
        return f'<rect x="{x + w // 5}" y="{y + h // 5}" width="{w * 3 // 5}" height="{h * 3 // 5}" rx="12" fill="{c}" stroke="#5B5550" stroke-width="3"/>'
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="16" fill="{c}"/>'


def _label_position(element: dict, index: int) -> tuple[int, int]:
    x = min(WIDTH - 180, max(12, element["x"]))
    if element["kind"] in {"fox", "rabbit", "hedgehog", "bird", "person"}:
        y = min(HEIGHT - 24, max(112, element["y"] + element["height"] + 28))
    elif element["kind"] == "action_lines":
        label = element["label"]
        if "哭声" in label:
            x, y = 820, 490
        elif "缠住脚" in label:
            x, y = 610, 680
        elif "剪断" in label:
            x, y = 560, 405
        elif "果子" in label:
            x, y = 500, 355
        else:
            y = min(HEIGHT - 24, max(112, element["y"] - 14))
    else:
        y = min(HEIGHT - 24, max(112, element["y"] + element["height"] + 22 + (index % 2) * 18))
    return x, y


def _theme(scene: dict) -> tuple[str, str, str, str, str]:
    text = " ".join(
        [
            str(scene.get("storyText") or ""),
            *(str(item.get("label") or "") for item in scene.get("elements", [])),
        ]
    )
    if any(word in text for word in ("夜", "晚上", "星星")):
        return "#182A49", "#435B7A", "#17233D", "#E9D58B", "night"
    if any(word in text for word in ("夕阳", "傍晚", "黄昏")):
        return "#F5B27A", "#F7D5A6", "#6E5572", "#FFF0B5", "sunset"
    if any(word in text for word in ("雨", "暴雨", "乌云")):
        return "#AFC6D4", "#D8E5E8", "#667D88", "#F5F0D0", "rain"
    return "#A9D8F0", "#E9F3D7", "#6A9A68", "#FFE49A", "day"


def _background(scene: dict) -> str:
    sky, lower, hill, sun, mode = _theme(scene)
    weather = (
        '<path d="M88 166 q25,-28 55,0 q28,-26 58,5 q-18,23 -126,17 Z" fill="#F7FBFC" opacity=".78"/>'
        '<path d="M860 210 q28,-30 58,0 q30,-25 62,6 q-18,22 -136,16 Z" fill="#F7FBFC" opacity=".78"/>'
        '<path d="M125 220 l-12 28 M170 220 l-12 28 M915 265 l-12 28 M960 265 l-12 28" fill="none" stroke="#718E9B" stroke-width="5" stroke-linecap="round" opacity=".65"/>'
        if mode == "rain"
        else '<path d="M90 150 q35,-32 70,0 q35,-30 70,4 q-18,25 -140,18 Z" fill="#FFF" opacity=".55"/><path d="M880 230 q32,-28 65,0 q32,-24 64,5 q-18,22 -130,16 Z" fill="#FFF" opacity=".55"/>'
    )
    celestial = (
        '<circle cx="1080" cy="150" r="52" fill="#F5F0D0"/><circle cx="210" cy="210" r="5" fill="#FFF4B1"/>'
        '<circle cx="300" cy="130" r="4" fill="#FFF4B1"/><circle cx="980" cy="270" r="5" fill="#FFF4B1"/>'
        if mode == "night"
        else f'<circle cx="1080" cy="{470 if mode == "sunset" else 150}" r="66" fill="{sun}" opacity=".9"/>'
    )
    return (
        f'<rect width="100%" height="100%" fill="{lower}"/>{celestial}{weather}'
        f'<path d="M0 470 Q180 360 360 470 T720 455 T1080 465 T1440 450 V720 H0 Z" fill="{hill}" opacity=".72"/>'
        '<path d="M0 535 Q190 445 390 535 T780 525 T1170 530 T1440 515 V720 H0 Z" fill="#A8D47D"/>'
        '<path d="M0 665 Q260 590 540 675 T1080 650 T1440 625 V720 H0 Z" fill="#6FA95D" opacity=".35"/>'
        '<path d="M72 666 l12 -22 m10 22 l12 -16 m1020 15 l12 -22 m10 22 l12 -16 m-760 24 l12 -20 m10 20 l12 -14" fill="none" stroke="#4F8B45" stroke-width="5" stroke-linecap="round" opacity=".7"/>'
        '<ellipse cx="145" cy="594" rx="18" ry="8" fill="#78966D" opacity=".45"/><ellipse cx="1110" cy="610" rx="22" ry="9" fill="#78966D" opacity=".4"/>'
    )


def _display_label(element: dict) -> str:
    label = element["label"]
    if any(mark in label for mark in ("<", ">", "&", '"', "'")):
        return label
    if element["kind"] in {"fox", "rabbit", "hedgehog", "person", "bird"}:
        for name in ("阿栗", "团团", "啄木鸟", "米米", "圆圆"):
            if name in label:
                return name
        return "角色"
    if element["kind"] == "action_lines":
        for words, short in (
            ("哭声", "哭声"),
            ("缠住脚", "缠住脚"),
            ("剪断", "剪断藤蔓"),
            ("果子", "送出果子"),
            ("约定", "约定"),
        ):
            if words in label:
                return short
        return "动作"
    if element["kind"] == "object" and any(word in label for word in ("溪", "桥", "藤", "果")):
        return label[:6]
    if "草坡" in label or "草地" in label:
        return ""
    if "蒲公英" in label:
        return "蒲公英"
    return ""


def _layer(element: dict) -> int:
    """按景深绘制，保证山坡在角色下面、动作在线条最上面。"""
    kind = element["kind"]
    label = element["label"]
    if kind == "action_lines":
        return 4
    if kind in {"fox", "rabbit", "hedgehog", "bird", "person"}:
        return 3
    if kind == "object" and "果" in label:
        return 3
    if kind == "object":
        return 2
    return 1


def _box_center(element: dict) -> tuple[int, int]:
    return element["x"] + element["width"] // 2, element["y"] + element["height"] // 2


def _find_element(raw_elements: list[dict], kind: str, words: tuple[str, ...] = ()) -> dict | None:
    for raw in raw_elements:
        element = _safe_element(raw, 0)
        kind_matches = element["kind"] == kind or (
            kind == "object" and element["kind"] in {"object", "simple_shape"}
        )
        if kind_matches and (not words or any(word in element["label"] for word in words)):
            return element
    return None


def _interaction(element: dict, raw_elements: list[dict]) -> str:
    label = element["label"]
    if element["kind"] != "action_lines":
        return ""
    if "剪断" in label:
        bird = _find_element(raw_elements, "bird")
        vine = _find_element(raw_elements, "object", ("藤", "树枝"))
        if not bird or not vine:
            return ""
        bx, by = bird["x"] + bird["width"] - 8, bird["y"] + bird["height"] // 2
        vx, vy = vine["x"] + vine["width"] // 2, vine["y"] + 10
        sx, sy = vx, vy - 24
        return (
            f'<path d="M{bx} {by} Q{(bx + vx) // 2} {by + 10} {vx} {vy}" fill="none" '
            f'stroke="#4F8B45" stroke-width="6" stroke-dasharray="10 10" stroke-linecap="round"/>'
            f'<path d="M{sx - 18} {sy - 18} l42 42 M{sx + 24} {sy - 18} l-42 42" '
            'stroke="#5B5550" stroke-width="7" stroke-linecap="round"/>'
            f'<circle cx="{sx - 18}" cy="{sy - 18}" r="10" fill="none" stroke="#5B5550" stroke-width="5"/>'
            f'<circle cx="{sx + 24}" cy="{sy - 18}" r="10" fill="none" stroke="#5B5550" stroke-width="5"/>'
        )
    if "果子" in label:
        fruit = _find_element(raw_elements, "object", ("果",))
        fox = _find_element(raw_elements, "fox")
        hedgehog = _find_element(raw_elements, "hedgehog")
        if not fruit or not fox or not hedgehog:
            return ""
        fx, fy = _box_center(fruit)
        fox_hand_x = fox["x"] + fox["width"] - 18
        fox_hand_y = fox["y"] + fox["height"] * 3 // 5
        hedgehog_hand_x = hedgehog["x"] + 18
        hedgehog_hand_y = hedgehog["y"] + hedgehog["height"] * 3 // 5
        fruit_left = fruit["x"] - 8
        fruit_right = fruit["x"] + fruit["width"] + 8
        arrow_end_x = fox["x"] + fox["width"] - 4
        arrow_end_y = fox_hand_y - 34
        return (
            f'<path d="M{hedgehog_hand_x} {hedgehog_hand_y} Q{fruit_right + 28} {fy + 34} {fruit_right} {fy + 12}" '
            'fill="none" stroke="#D9B38C" stroke-width="16" stroke-linecap="round"/>'
            f'<circle cx="{fruit_right}" cy="{fy + 12}" r="10" fill="#D9B38C" stroke="#5B5550" stroke-width="3"/>'
            f'<path d="M{fox_hand_x} {fox_hand_y} Q{fruit_left - 26} {fy + 42} {fruit_left} {fy + 22}" '
            'fill="none" stroke="#E87824" stroke-width="14" stroke-linecap="round"/>'
            f'<circle cx="{fruit_left}" cy="{fy + 22}" r="9" fill="#D9B38C" stroke="#5B5550" stroke-width="3"/>'
            f'<path d="M{fruit_right + 4} {fy + 72} Q{(fruit_right + arrow_end_x) // 2} {fy + 120} {arrow_end_x} {arrow_end_y}" '
            'fill="none" stroke="#D93636" stroke-width="6" stroke-dasharray="11 11" stroke-linecap="round"/>'
            f'<path d="M{arrow_end_x} {arrow_end_y} l22 -10 M{arrow_end_x} {arrow_end_y} l18 14" '
            'stroke="#D93636" stroke-width="6" stroke-linecap="round"/>'
        )
    if "约定" in label:
        fox = _find_element(raw_elements, "fox")
        hedgehog = _find_element(raw_elements, "hedgehog")
        if not fox or not hedgehog:
            return ""
        left = fox["x"] + fox["width"]
        right = hedgehog["x"]
        y = min(fox["y"], hedgehog["y"]) + 82
        mid = (left + right) // 2
        return (
            f'<path d="M{left} {y} Q{mid - 35} {y - 42} {mid} {y}" fill="none" '
            f'stroke="#6B7280" stroke-width="7" stroke-linecap="round"/>'
            f'<path d="M{right} {y} Q{mid + 35} {y - 42} {mid} {y}" fill="none" '
            'stroke="#6B7280" stroke-width="7" stroke-linecap="round"/>'
            f'<path d="M{mid - 12} {y + 3} q12 20 24 0" fill="none" stroke="#E5A83B" stroke-width="6" stroke-linecap="round"/>'
        )
    if "缠住脚" in label:
        hedgehog = _find_element(raw_elements, "hedgehog")
        if not hedgehog:
            return ""
        foot_x = hedgehog["x"] + hedgehog["width"] // 2
        foot_y = hedgehog["y"] + hedgehog["height"] * 4 // 5
        return (
            f'<ellipse cx="{foot_x - 22}" cy="{foot_y}" rx="26" ry="13" fill="none" stroke="#2F6D35" stroke-width="7"/>'
            f'<ellipse cx="{foot_x + 26}" cy="{foot_y + 4}" rx="26" ry="13" fill="none" stroke="#2F6D35" stroke-width="7"/>'
            f'<path d="M{foot_x - 40} {foot_y + 25} q42 18 84 0" fill="none" stroke="#2F6D35" stroke-width="5" stroke-linecap="round"/>'
        )
    if "哭声" in label:
        hedgehog = _find_element(raw_elements, "hedgehog")
        fox = _find_element(raw_elements, "fox")
        water = _find_element(raw_elements, "object", ("溪", "水"))
        source = hedgehog or water or fox
        target = fox if source is water else (fox if hedgehog else None)
        if not source or not target:
            return ""
        cx = source["x"] + source["width"] // 2
        cy = source["y"] + (source["height"] // 3 if source is water else 32)
        tx = target["x"] + target["width"] // 2
        direction = -1 if tx < cx else 1
        sound = (
            f'<path d="M{cx + direction * 20} {cy} q{direction * 45} -35 {direction * 90} 0 '
            f'M{cx + direction * 40} {cy + 30} q{direction * 35} -25 {direction * 70} 0" '
            'fill="none" stroke="#5B5550" stroke-width="7" stroke-linecap="round"/>'
            f'<path d="M{tx} {cy} l{direction * 22} 12 l{direction * -22} 12" fill="none" '
            'stroke="#5B5550" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/>'
        )
        if source["kind"] != "hedgehog":
            return sound
        foot_x = source["x"] + source["width"] // 2
        foot_y = source["y"] + source["height"] * 4 // 5
        return (
            f'{sound}<ellipse cx="{foot_x - 22}" cy="{foot_y}" rx="26" ry="13" fill="none" stroke="#2F6D35" stroke-width="7"/>'
            f'<ellipse cx="{foot_x + 26}" cy="{foot_y + 4}" rx="26" ry="13" fill="none" stroke="#2F6D35" stroke-width="7"/>'
        )
    return ""


def scene_to_svg(scene: dict) -> str:
    title = escape(str(scene.get("title") or ""), quote=True) if isinstance(scene, dict) else ""
    summary = (
        escape(str(scene.get("storyText") or ""), quote=True) if isinstance(scene, dict) else ""
    )
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" width="{WIDTH}" height="{HEIGHT}">',
        _background(scene),
        '<rect x="24" y="20" width="1232" height="62" rx="14" fill="#FFF9EE" opacity=".94"/>',
    ]
    if title or summary:
        parts.append(
            f'<text x="48" y="48" fill="#3D382F" font-size="22" font-family="sans-serif" font-weight="600">{title or "故事这一幕"}</text>'
        )
        if summary:
            parts.append(
                f'<text x="48" y="70" fill="#6B6256" font-size="13" font-family="sans-serif">{summary[:90]}</text>'
            )
    raw_elements = scene.get("elements", []) if isinstance(scene, dict) else []
    if not isinstance(raw_elements, list):
        raw_elements = []
    prepared = [
        (_safe_element(raw_element, index), index)
        for index, raw_element in enumerate(raw_elements[:8])
    ]
    prepared.sort(key=lambda pair: (_layer(pair[0]), pair[1]))
    ordered = [item for item, _ in prepared]
    for element in ordered:
        parts.append(
            f'<g data-start-ms="{int(element.get("startMs", 0))}" data-duration-ms="{int(element.get("durationMs", 0))}">{_shape(element)}</g>'
        )
    for element in ordered:
        if interaction := _interaction(element, ordered):
            parts.append(interaction)
    for element in ordered:
        if element["kind"] in {"fox", "rabbit", "hedgehog", "bird", "person"}:
            cx = element["x"] + element["width"] // 2
            cy = min(700, element["y"] + element["height"] - 4)
            parts.append(
                f'<ellipse cx="{cx}" cy="{cy}" rx="{max(44, element["width"] // 3)}" ry="14" fill="#4B7048" opacity=".18"/>'
            )
    label_boxes: list[tuple[int, int, int, int]] = []
    seen_labels: set[str] = set()
    for index, element in enumerate(ordered):
        label = escape(_display_label(element), quote=True)
        label_key = label or element["label"]
        if label_key == "蒲公英" and label_key in seen_labels:
            continue
        seen_labels.add(label_key)
        label_x, label_y = _label_position(element, index)
        if label:
            label_width = min(150, max(60, len(label) * 14))
            while any(
                label_x - 5 < right
                and label_x - 5 + label_width > left
                and label_y - 16 < bottom
                and label_y + 6 > top
                for left, top, right, bottom in label_boxes
            ):
                label_y += 26
                if label_y > HEIGHT - 24:
                    label_y = 112
                    label_x = min(WIDTH - label_width - 8, label_x + 90)
            label_boxes.append((label_x - 5, label_y - 16, label_x - 5 + label_width, label_y + 6))
            if element["kind"] == "action_lines":
                parts.append(
                    f'<text x="{label_x}" y="{label_y}" fill="#5B5550" font-size="15" font-family="sans-serif" font-weight="600" stroke="#FFF9EE" stroke-width="5" paint-order="stroke">{label[:18]}</text>'
                )
            else:
                parts.append(
                    f'<rect x="{label_x - 5}" y="{label_y - 16}" width="{label_width}" height="22" rx="5" fill="#FFF9EE" opacity=".88"/>'
                    f'<text x="{label_x}" y="{label_y}" fill="#5B5550" font-size="16" font-family="sans-serif">{label[:18]}</text>'
                )
    parts.append("</svg>")
    return "".join(parts)
