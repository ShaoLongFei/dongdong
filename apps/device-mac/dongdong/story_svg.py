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
        spikes = " ".join(
            f"M{x + w * n // 10},{y + h // 2} l{-18 if n % 2 else 18},{-h // 4}"
            for n in range(2, 9)
        )
        return (
            f'<path d="{spikes}" fill="none" stroke="#5B5550" stroke-width="7" stroke-linecap="round"/>'
            f'<ellipse cx="{cx}" cy="{cy + 10}" rx="{w // 2 - 7}" ry="{h // 3}" fill="{c}" stroke="#5B5550" stroke-width="4"/>'
            f'<ellipse cx="{x + w * 3 // 4}" cy="{cy - 2}" rx="{w // 5}" ry="{h // 5}" fill="#D9B38C" stroke="#5B5550" stroke-width="3"/>'
            f'<circle cx="{x + w * 4 // 5}" cy="{cy - 8}" r="5" fill="#3D382F"/><circle cx="{x + w * 9 // 10}" cy="{cy + 4}" r="6" fill="#3D382F"/>'
            f'<path d="M{x + w // 4},{y + h * 4 // 5} h18 M{x + w * 2 // 3},{y + h * 4 // 5} h18" stroke="#5B5550" stroke-width="7" stroke-linecap="round"/>'
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
    if "草坡" in label or "草地" in label:
        return f'<path d="M{x},{y + h} Q{x + w // 4},{y + h // 2} {x + w // 2},{y + h * 3 // 4} T{x + w},{y + h // 2} V{y + h} Z" fill="{c}"/>'
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="16" fill="{c}"/>'


def _label_position(element: dict, index: int) -> tuple[int, int]:
    x = min(WIDTH - 180, max(12, element["x"]))
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
            ("剪断", "剪断藤蔓"),
            ("果子", "送出果子"),
            ("约定", "约定"),
        ):
            if words in label:
                return short
        return "动作"
    if element["kind"] == "object" and any(word in label for word in ("溪", "桥", "藤", "果")):
        return label[:6]
    if "蒲公英" in label:
        return "蒲公英"
    return ""


def _interaction(element: dict, raw_elements: list[dict]) -> str:
    label = element["label"]
    if element["kind"] != "action_lines":
        return ""
    if "剪断" in label:
        return '<path d="M730 390 l-44,42 M750 390 l-44,42" stroke="#5B5550" stroke-width="7" stroke-linecap="round"/><path d="M686 432 l-15,-5 l8,-13 M706 432 l15,-5 l-8,-13" fill="none" stroke="#5B5550" stroke-width="5"/> '
    if "果子" in label:
        return '<path d="M490 380 Q590 330 690 380" fill="none" stroke="#D93636" stroke-width="7" stroke-dasharray="12 12"/><path d="M685 380 l-20,-12 M685 380 l-20,12" stroke="#D93636" stroke-width="7" stroke-linecap="round"/> '
    if "约定" in label:
        return '<path d="M590 365 q35,-30 70,0 M660 365 q35,-30 70,0" fill="none" stroke="#6B7280" stroke-width="7" stroke-linecap="round"/> '
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
    for index, raw_element in enumerate(raw_elements[:8]):
        element = _safe_element(raw_element, index)
        label = escape(_display_label(element), quote=True)
        label_x, label_y = _label_position(element, index)
        parts.append(
            f'<g data-start-ms="{int(element.get("startMs", 0))}" data-duration-ms="{int(element.get("durationMs", 0))}">{_shape(element)}'
        )
        if interaction := _interaction(element, raw_elements):
            parts.append(interaction)
        if label:
            parts.append(
                f'<rect x="{label_x - 5}" y="{label_y - 16}" width="{min(150, max(60, len(label) * 14))}" height="22" rx="5" fill="#FFF9EE" opacity=".88"/>'
                f'<text x="{label_x}" y="{label_y}" fill="#5B5550" font-size="16" font-family="sans-serif">{label[:18]}</text>'
            )
        parts.append("</g>")
    parts.append("</svg>")
    return "".join(parts)
