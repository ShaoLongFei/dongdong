"""把受控故事元素绘制为无外部依赖的 SVG。"""

from __future__ import annotations

from html import escape

from .story_schema import ALLOWED_COLORS, ALLOWED_KINDS, BACKGROUND, HEIGHT, WIDTH


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
        "color": color if isinstance(color, str) and color in ALLOWED_COLORS else "#5B5550",
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
    if kind == "tree":
        return f'<rect x="{x + w // 2 - 16}" y="{y + h // 2}" width="32" height="{h // 2}" fill="{c}"/><circle cx="{x + w // 2}" cy="{y + h // 3}" r="{min(w, h) // 3}" fill="{c}"/>'
    if kind == "rabbit":
        return f'<ellipse cx="{x + w // 2}" cy="{y + h // 2}" rx="{w // 3}" ry="{h // 3}" fill="{c}"/><circle cx="{x + w // 2}" cy="{y + h // 4}" r="{min(w, h) // 5}" fill="{c}"/><path d="M{x + w // 2 - 22},{y + h // 5} l-12,-{h // 4} l28,18 M{x + w // 2 + 22},{y + h // 5} l12,-{h // 4} l-28,18" fill="none" stroke="{c}" stroke-width="10"/>'
    if kind == "person":
        return f'<circle cx="{x + w // 2}" cy="{y + h // 4}" r="{min(w, h) // 7}" fill="{c}"/><path d="M{x + w // 2},{y + h // 3} v{h // 3} m-{w // 5},-{h // 6} h{w // 2} m-{w // 8},{h // 3} l-{w // 6},{h // 4} m{w // 8},{h // 4} l{w // 6},{h // 4}" fill="none" stroke="{c}" stroke-width="12" stroke-linecap="round"/>'
    if kind == "house":
        return f'<path d="M{x},{y + h // 3} L{x + w // 2},{y} L{x + w},{y + h // 3} V{y + h} H{x} Z" fill="{c}"/><rect x="{x + w // 3}" y="{y + h // 2}" width="{w // 3}" height="{h // 2}" fill="{BACKGROUND}"/>'
    if kind == "sun":
        return f'<circle cx="{x + w // 2}" cy="{y + h // 2}" r="{min(w, h) // 3}" fill="{c}"/><circle cx="{x + w // 2}" cy="{y + h // 2}" r="{min(w, h) // 2 - 8}" fill="none" stroke="{c}" stroke-width="6" stroke-dasharray="12 14"/>'
    if kind == "action_lines":
        return f'<path d="M{x},{y + h // 2} h{w // 3} m{w // 6},-{h // 4} h{w // 3} m-{w // 3},{h // 2} h{w // 3}" fill="none" stroke="{c}" stroke-width="8" stroke-linecap="round"/>'
    if kind == "object":
        return f'<rect x="{x + w // 5}" y="{y + h // 5}" width="{w * 3 // 5}" height="{h * 3 // 5}" rx="12" fill="{c}"/>'
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="16" fill="{c}"/>'


def scene_to_svg(scene: dict) -> str:
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" width="{WIDTH}" height="{HEIGHT}">',
        f'<rect width="100%" height="100%" fill="{BACKGROUND}"/>',
    ]
    raw_elements = scene.get("elements", []) if isinstance(scene, dict) else []
    if not isinstance(raw_elements, list):
        raw_elements = []
    for index, raw_element in enumerate(raw_elements[:8]):
        element = _safe_element(raw_element, index)
        label = escape(element["label"], quote=True)
        parts.append(
            f'<g data-start-ms="{int(element.get("startMs", 0))}" data-duration-ms="{int(element.get("durationMs", 0))}">{_shape(element)}'
        )
        if label:
            parts.append(
                f'<text x="{element["x"]}" y="{min(HEIGHT - 8, element["y"] + element["height"] + 24)}" fill="#5B5550">{label}</text>'
            )
        parts.append("</g>")
    parts.append("</svg>")
    return "".join(parts)
