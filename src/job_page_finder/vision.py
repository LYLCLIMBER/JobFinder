from __future__ import annotations

import base64
import io
import math
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageDraw, ImageFont


@dataclass(frozen=True)
class VisualContext:
    """A screenshot annotated with the indexes the vision model may use."""

    image_data_url: str
    annotated_indexes: tuple[int, ...]


def build_visual_context(
    state: Any,
    *,
    max_candidates: int = 20,
    min_visible_fraction: float = 0.2,
) -> VisualContext | None:
    """Annotate reliable textless interactive elements in the current screenshot."""
    screenshot = getattr(state, "screenshot", None)
    page_info = getattr(state, "page_info", None)
    if not screenshot or page_info is None or max_candidates < 1:
        return None

    try:
        image = Image.open(io.BytesIO(base64.b64decode(screenshot))).convert("RGBA")
    except Exception:
        return None

    try:
        candidates = _find_candidates(state, page_info, image.size, max_candidates, min_visible_fraction)
        if not candidates:
            return None

        draw = ImageDraw.Draw(image)
        font = _load_font()
        for index, box in candidates:
            _draw_candidate(draw, box, index, font, image.size)

        output = io.BytesIO()
        image.save(output, format="PNG")
        encoded = base64.b64encode(output.getvalue()).decode("ascii")
        return VisualContext(
            image_data_url=f"data:image/png;base64,{encoded}",
            annotated_indexes=tuple(index for index, _ in candidates),
        )
    except Exception:
        return None
    finally:
        image.close()


def _find_candidates(
    state: Any,
    page_info: Any,
    image_size: tuple[int, int],
    max_candidates: int,
    min_visible_fraction: float,
) -> list[tuple[int, tuple[int, int, int, int]]]:
    viewport_width = _positive_number(getattr(page_info, "viewport_width", None))
    viewport_height = _positive_number(getattr(page_info, "viewport_height", None))
    if viewport_width is None or viewport_height is None:
        return []

    scroll_x = _finite_number(getattr(page_info, "scroll_x", 0)) or 0
    scroll_y = _finite_number(getattr(page_info, "scroll_y", 0)) or 0
    image_width, image_height = image_size
    selector_map = getattr(getattr(state, "dom_state", None), "selector_map", {})
    candidates: list[tuple[int, tuple[int, int, int, int], float]] = []

    for index, node in selector_map.items():
        if not _is_textless(node) or getattr(node, "is_visible", None) is False:
            continue

        bounds = getattr(node, "absolute_position", None)
        if bounds is None:
            continue
        coordinates = [_finite_number(getattr(bounds, field, None)) for field in ("x", "y", "width", "height")]
        if any(value is None for value in coordinates):
            continue
        x, y, width, height = coordinates
        if width <= 0 or height <= 0:
            continue

        viewport_box = (x - scroll_x, y - scroll_y, x - scroll_x + width, y - scroll_y + height)
        visible_box = _intersect(viewport_box, (0, 0, viewport_width, viewport_height))
        if visible_box is None:
            continue
        visible_area = (visible_box[2] - visible_box[0]) * (visible_box[3] - visible_box[1])
        if visible_area / (width * height) < min_visible_fraction:
            continue

        pixel_box = _to_pixel_box(visible_box, viewport_width, viewport_height, image_size)
        if pixel_box is None:
            continue
        pixel_area = (pixel_box[2] - pixel_box[0]) * (pixel_box[3] - pixel_box[1])
        candidates.append((int(index), pixel_box, pixel_area))

    candidates.sort(key=lambda candidate: (-candidate[2], candidate[0]))
    return [(index, box) for index, box, _ in candidates[:max_candidates]]


def _is_textless(node: Any) -> bool:
    meaningful_text = getattr(node, "get_meaningful_text_for_llm", lambda: "")()
    if str(meaningful_text).strip():
        return False

    attributes = getattr(node, "attributes", {}) or {}
    semantic_attributes = ("value", "aria-label", "title", "placeholder", "alt")
    if any(str(attributes.get(name, "") or "").strip() for name in semantic_attributes):
        return False

    ax_node = getattr(node, "ax_node", None)
    if ax_node is not None and any(str(getattr(ax_node, field, "") or "").strip() for field in ("name", "description")):
        return False

    return not _has_semantic_descendant(node)


def _has_semantic_descendant(node: Any) -> bool:
    stack = list(getattr(node, "children", []) or [])
    visited = 0
    while stack and visited < 100:
        child = stack.pop()
        visited += 1
        attributes = getattr(child, "attributes", {}) or {}
        if any(str(attributes.get(name, "") or "").strip() for name in ("aria-label", "title", "alt")):
            return True
        stack.extend(getattr(child, "children", []) or [])
    return False


def _to_pixel_box(
    box: tuple[float, float, float, float],
    viewport_width: float,
    viewport_height: float,
    image_size: tuple[int, int],
) -> tuple[int, int, int, int] | None:
    image_width, image_height = image_size
    scale_x = image_width / viewport_width
    scale_y = image_height / viewport_height
    x1 = max(0, min(image_width, math.floor(box[0] * scale_x)))
    y1 = max(0, min(image_height, math.floor(box[1] * scale_y)))
    x2 = max(0, min(image_width, math.ceil(box[2] * scale_x)))
    y2 = max(0, min(image_height, math.ceil(box[3] * scale_y)))
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2, y2


def _intersect(
    first: tuple[float, float, float, float], second: tuple[float, float, float, float]
) -> tuple[float, float, float, float] | None:
    x1 = max(first[0], second[0])
    y1 = max(first[1], second[1])
    x2 = min(first[2], second[2])
    y2 = min(first[3], second[3])
    return (x1, y1, x2, y2) if x2 > x1 and y2 > y1 else None


def _draw_candidate(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    index: int,
    font: ImageFont.ImageFont,
    image_size: tuple[int, int],
) -> None:
    x1, y1, x2, y2 = box
    draw.rectangle(box, outline="#ff3b30", width=3)
    label = f"[{index}]"
    text_box = draw.textbbox((0, 0), label, font=font)
    text_width = text_box[2] - text_box[0]
    text_height = text_box[3] - text_box[1]
    padding = 4
    label_x = max(0, min(image_size[0] - text_width - padding * 2, (x1 + x2 - text_width) // 2 - padding))
    label_y = y1 - text_height - padding * 2 - 2
    if label_y < 0:
        label_y = min(image_size[1] - text_height - padding * 2, y1 + 2)
    label_box = (label_x, label_y, label_x + text_width + padding * 2, label_y + text_height + padding * 2)
    draw.rectangle(label_box, fill="white", outline="#ff3b30", width=2)
    draw.text((label_x + padding, label_y + padding - text_box[1]), label, fill="black", font=font)


def _load_font() -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf", 16)
    except OSError:
        return ImageFont.load_default()


def _finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _positive_number(value: Any) -> float | None:
    number = _finite_number(value)
    return number if number is not None and number > 0 else None
