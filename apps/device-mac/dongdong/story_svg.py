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
        return f'<path d="M{x + w // 2},{y} L{x + w // 5},{y + h // 3} V{y + h} H{x + w * 4 // 5} V{y + h // 3} Z" fill="{c}"/><circle cx="{x + w // 2}" cy="{y + h // 2}" r="{min(w, h) // 4}" fill="#F5EBD7"/><circle cx="{x + w * 2 // 5}" cy="{y + h // 2 - 8}" r="5" fill="#5B5550"/><circle cx="{x + w * 3 // 5}" cy="{y + h // 2 - 8}" r="5" fill="#5B5550"/><path d="M{x + w // 2 - 8},{y + h // 2 + 8} l8,8 l8,-8" fill="none" stroke="#5B5550" stroke-width="4"/>'
    if kind == "hedgehog":
        return f'<ellipse cx="{x + w // 2}" cy="{y + h // 2}" rx="{w // 2 - 8}" ry="{h // 3}" fill="{c}"/><path d="M{x + w // 4},{y + h // 3} l-18,-28 m{w // 4 + 18},28 l0,-35 m28,35 l18,-28" fill="none" stroke="#5B5550" stroke-width="8" stroke-linecap="round"/><circle cx="{x + w * 3 // 4}" cy="{y + h // 2 - 8}" r="5" fill="#5B5550"/>'
    if kind == "bird":
        return f'<ellipse cx="{x + w // 2}" cy="{y + h // 2}" rx="{w // 3}" ry="{h // 3}" fill="{c}"/><circle cx="{x + w * 2 // 3}" cy="{y + h // 3}" r="{min(w, h) // 5}" fill="{c}"/><path d="M{x + w * 2 // 3 + 18},{y + h // 3} l28,10 l-28,10 M{x + w // 3},{y + h // 2} l-{w // 4},{h // 5}" fill="none" stroke="{c}" stroke-width="8" stroke-linecap="round"/>'
    if kind == "person":
        return f'<circle cx="{x + w // 2}" cy="{y + h // 4}" r="{min(w, h) // 7}" fill="{c}"/><path d="M{x + w // 2},{y + h // 3} v{h // 3} m-{w // 5},-{h // 6} h{w // 2} m-{w // 8},{h // 3} l-{w // 6},{h // 4} m{w // 8},{h // 4} l{w // 6},{h // 4}" fill="none" stroke="{c}" stroke-width="12" stroke-linecap="round"/>'
    if kind == "house":
        return f'<path d="M{x},{y + h // 3} L{x + w // 2},{y} L{x + w},{y + h // 3} V{y + h} H{x} Z" fill="{c}"/><rect x="{x + w // 3}" y="{y + h // 2}" width="{w // 3}" height="{h // 2}" fill="{BACKGROUND}"/>'
    if kind == "sun":
        return f'<circle cx="{x + w // 2}" cy="{y + h // 2}" r="{min(w, h) // 3}" fill="{c}"/><circle cx="{x + w // 2}" cy="{y + h // 2}" r="{min(w, h) // 2 - 8}" fill="none" stroke="{c}" stroke-width="6" stroke-dasharray="12 14"/>'
    if kind == "action_lines":
        return f'<path d="M{x},{y + h // 2} h{w // 3} m{w // 6},-{h // 4} h{w // 3} m-{w // 3},{h // 2} h{w // 3}" fill="none" stroke="{c}" stroke-width="8" stroke-linecap="round"/>'
    if kind == "object":
        if "溪" in label or "水" in label:
            return f'<path d="M{x},{y + h // 2} q{w // 8},-{h // 2} {w // 4},0 t{w // 4},0 t{w // 4},0 t{w // 4},0 V{y + h} H{x} Z" fill="{c}"/>'
        if "桥" in label:
            return f'<rect x="{x}" y="{y + h // 3}" width="{w}" height="{h // 3}" rx="8" fill="{c}"/><path d="M{x + 15},{y + h // 3} V{y + h} M{x + w - 15},{y + h // 3} V{y + h}" stroke="#5B5550" stroke-width="8"/>'
        if "果" in label:
            return f'<circle cx="{x + w // 2}" cy="{y + h // 2 + 8}" r="{min(w, h) // 3}" fill="{c}"/><path d="M{x + w // 2},{y + h // 3} q12,-22 30,-8" fill="none" stroke="#4F8B45" stroke-width="8"/>'
        if "藤" in label or "树枝" in label:
            return f'<path d="M{x},{y + h // 2} q{w // 4},-{h} {w // 2},0 t{w // 2},0" fill="none" stroke="{c}" stroke-width="{max(8, h // 2)}" stroke-linecap="round"/>'
        return f'<rect x="{x + w // 5}" y="{y + h // 5}" width="{w * 3 // 5}" height="{h * 3 // 5}" rx="12" fill="{c}"/>'
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="16" fill="{c}"/>'


def _label_position(element: dict, index: int) -> tuple[int, int]:
    x = min(WIDTH - 180, max(12, element["x"]))
    y = min(HEIGHT - 24, max(112, element["y"] + element["height"] + 22 + (index % 2) * 18))
    return x, y


def scene_to_svg(scene: dict) -> str:
    title = escape(str(scene.get("title") or ""), quote=True) if isinstance(scene, dict) else ""
    summary = (
        escape(str(scene.get("storyText") or ""), quote=True) if isinstance(scene, dict) else ""
    )
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" width="{WIDTH}" height="{HEIGHT}">',
        f'<rect width="100%" height="100%" fill="{BACKGROUND}"/>',
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
        label = escape(element["label"], quote=True)
        label_x, label_y = _label_position(element, index)
        parts.append(
            f'<g data-start-ms="{int(element.get("startMs", 0))}" data-duration-ms="{int(element.get("durationMs", 0))}">{_shape(element)}'
        )
        if label:
            parts.append(
                f'<rect x="{label_x - 5}" y="{label_y - 16}" width="{min(210, max(60, len(element["label"]) * 14))}" height="22" rx="5" fill="#FFF9EE" opacity=".88"/>'
                f'<text x="{label_x}" y="{label_y}" fill="#5B5550" font-size="16" font-family="sans-serif">{label[:18]}</text>'
            )
        parts.append("</g>")
    parts.append("</svg>")
    return "".join(parts)
