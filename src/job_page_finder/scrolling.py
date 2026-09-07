from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

ScrollDirection = Literal["up", "down"]


@dataclass(frozen=True)
class ScrollTarget:
    index: int
    node: Any | None
    offset: float
    remaining_up: float
    remaining_down: float
    bounds: Any | None = None
    in_active_modal: bool = False

    @property
    def backend_node_id(self) -> int | None:
        return getattr(self.node, "backend_node_id", None)

    def can_scroll(self, direction: ScrollDirection) -> bool:
        return self.remaining_down > 0 if direction == "down" else self.remaining_up > 0


def discover_scroll_targets(state: Any) -> tuple[ScrollTarget, ...]:
    """Build a step-local target list without changing browser-use's selector map."""
    dom_state = getattr(state, "dom_state", None)
    selector_map = getattr(dom_state, "selector_map", {}) or {}
    indexes_by_backend_id = {
        backend_node_id: int(index)
        for index, node in selector_map.items()
        if (backend_node_id := getattr(node, "backend_node_id", None)) is not None
    }
    next_index = max((int(index) for index in selector_map), default=0) + 1
    targets: list[ScrollTarget] = []

    # browser-use currently exposes no public traversal API for the complete serialized tree.
    root = getattr(dom_state, "_root", None)
    for simplified_node in _walk_simplified_tree(root):
        node = getattr(simplified_node, "original_node", None)
        if node is None or getattr(node, "tag_name", "").lower() in {"html", "body"}:
            continue
        if getattr(node, "is_visible", None) is not True or not getattr(node, "is_actually_scrollable", False):
            continue
        scroll_info = getattr(node, "scroll_info", None)
        bounds = getattr(node, "absolute_position", None)
        if not scroll_info or bounds is None or not _is_in_viewport(bounds, getattr(state, "page_info", None)):
            continue

        remaining_up = float(scroll_info.get("content_above", 0) or 0)
        remaining_down = float(scroll_info.get("content_below", 0) or 0)
        if remaining_up <= 0 and remaining_down <= 0:
            continue

        backend_node_id = getattr(node, "backend_node_id", None)
        index = indexes_by_backend_id.get(backend_node_id)
        if index is None:
            index = next_index
            next_index += 1
        targets.append(
            ScrollTarget(
                index=index,
                node=node,
                offset=float(scroll_info.get("scroll_top", 0) or 0),
                remaining_up=remaining_up,
                remaining_down=remaining_down,
                bounds=bounds,
                in_active_modal=_is_in_active_modal(node),
            )
        )

    targets.sort(key=lambda target: not target.in_active_modal)
    return tuple(targets)


def root_scroll_target(state: Any) -> ScrollTarget:
    page_info = getattr(state, "page_info", None)
    return ScrollTarget(
        index=0,
        node=None,
        offset=float(getattr(page_info, "scroll_y", 0) or 0),
        remaining_up=float(getattr(page_info, "pixels_above", 0) or 0),
        remaining_down=float(getattr(page_info, "pixels_below", 0) or 0),
    )


def find_scroll_target(state: Any, target: ScrollTarget) -> ScrollTarget | None:
    if target.index == 0:
        return root_scroll_target(state)
    backend_node_id = target.backend_node_id
    if backend_node_id is None:
        return None
    for candidate in discover_scroll_targets(state):
        if candidate.backend_node_id == backend_node_id:
            return ScrollTarget(
                index=target.index,
                node=candidate.node,
                offset=candidate.offset,
                remaining_up=candidate.remaining_up,
                remaining_down=candidate.remaining_down,
                bounds=candidate.bounds,
                in_active_modal=candidate.in_active_modal,
            )
    return None


def describe_scroll_targets(state: Any, targets: tuple[ScrollTarget, ...]) -> str:
    root = root_scroll_target(state)
    lines = [_describe_target(root, "root page")]
    for target in targets:
        node = target.node
        tag = getattr(node, "tag_name", "element")
        text = getattr(node, "get_meaningful_text_for_llm", lambda: "")().strip()
        label = f"<{tag}>"
        if text:
            label += f" {text[:120]}"
        if target.in_active_modal:
            label += " (active modal)"
        lines.append(_describe_target(target, label))
    return "\n".join(lines)


def targets_for_direction(targets: tuple[ScrollTarget, ...], direction: ScrollDirection) -> tuple[ScrollTarget, ...]:
    return tuple(target for target in targets if target.can_scroll(direction))


def _describe_target(target: ScrollTarget, label: str) -> str:
    directions = []
    if target.remaining_up > 0:
        directions.append(f"up {target.remaining_up:g}px")
    if target.remaining_down > 0:
        directions.append(f"down {target.remaining_down:g}px")
    availability = ", ".join(directions) if directions else "no remaining scroll space"
    return f"[{target.index}] {label}: offset {target.offset:g}px; {availability}"


def _walk_simplified_tree(root: Any):
    if root is None or not hasattr(root, "original_node"):
        return
    stack = [root]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(getattr(current, "children", []) or []))


def _is_in_viewport(bounds: Any, page_info: Any) -> bool:
    if page_info is None:
        return False
    width = float(getattr(bounds, "width", 0) or 0)
    height = float(getattr(bounds, "height", 0) or 0)
    if width <= 0 or height <= 0:
        return False
    x = float(getattr(bounds, "x", 0) or 0) - float(getattr(page_info, "scroll_x", 0) or 0)
    y = float(getattr(bounds, "y", 0) or 0) - float(getattr(page_info, "scroll_y", 0) or 0)
    viewport_width = float(getattr(page_info, "viewport_width", 0) or 0)
    viewport_height = float(getattr(page_info, "viewport_height", 0) or 0)
    return x < viewport_width and y < viewport_height and x + width > 0 and y + height > 0


def _is_in_active_modal(node: Any) -> bool:
    current = node
    while current is not None:
        attributes = getattr(current, "attributes", {}) or {}
        role = str(attributes.get("role", "")).lower()
        aria_modal = str(attributes.get("aria-modal", "")).lower()
        tag_name = str(getattr(current, "tag_name", "")).lower()
        if role == "dialog" or aria_modal == "true" or (tag_name == "dialog" and "open" in attributes):
            return getattr(current, "is_visible", None) is not False
        current = getattr(current, "parent_node", None)
    return False
