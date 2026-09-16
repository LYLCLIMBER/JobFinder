from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

from job_page_finder.adapters.browser_use.scroll_targets import (
    ScrollTargetIndex,
    discover_scroll_target_index,
)
from job_page_finder.adapters.browser_use.vision import build_visual_snapshot
from job_page_finder.core_models import (
    ElementRef,
    InteractiveElement,
    PageObservation,
    VisualCandidate,
)


@dataclass
class ObservationConversion:
    observation: PageObservation
    element_map: dict[ElementRef, Any]
    scroll_index: ScrollTargetIndex


def build_observation(
    state: Any,
    *,
    include_image: bool,
    generation: int = 0,
    max_dom_characters: int = 40_000,
    max_visual_candidates: int = 20,
) -> ObservationConversion:
    dom_state = getattr(state, "dom_state", None)
    selector_map = getattr(dom_state, "selector_map", {}) or {}
    refs_by_index = {int(index): ElementRef(f"observation:{generation}:index:{int(index)}") for index in selector_map}
    element_map = {refs_by_index[int(index)]: node for index, node in selector_map.items()}
    elements = tuple(
        InteractiveElement(
            ref=refs_by_index[int(index)],
            text=str(getattr(node, "get_meaningful_text_for_llm", lambda: "")()).strip(),
            role=_attribute(node, "role"),
            href=_attribute(node, "href"),
        )
        for index, node in selector_map.items()
    )
    scroll_index = discover_scroll_target_index(state, refs_by_index=refs_by_index, generation=generation)
    refs_for_visuals = dict(refs_by_index)
    refs_for_visuals.update(scroll_index.refs_by_index)
    snapshot = None
    if include_image:
        snapshot = build_visual_snapshot(
            state,
            element_refs=refs_for_visuals,
            max_candidates=max_visual_candidates,
            scroll_targets=scroll_index.internal_targets,
        )
    dom_text = str(getattr(dom_state, "llm_representation", lambda: "")())
    screenshot = snapshot.screenshot if snapshot else _decode_screenshot(getattr(state, "screenshot", None))
    observation = PageObservation(
        url=str(getattr(state, "url", "")),
        title=str(getattr(state, "title", "")),
        visible_text=dom_text,
        elements=elements,
        scroll_targets=scroll_index.public_targets,
        screenshot=screenshot,
        visual_candidates=tuple(
            VisualCandidate(ref=ref, box=box) for ref, box in (snapshot.candidate_boxes.items() if snapshot else ())
        ),
        is_usable=getattr(state, "state_error", None) is None and getattr(dom_state, "_root", None) is not None,
        error=str(getattr(state, "state_error", "") or "") or None,
    )
    return ObservationConversion(observation=observation, element_map=element_map, scroll_index=scroll_index)


def _attribute(node: Any, name: str) -> str | None:
    value = (getattr(node, "attributes", {}) or {}).get(name)
    return str(value) if value is not None else None


def _decode_screenshot(value: Any) -> bytes | None:
    if not value:
        return None
    try:
        return base64.b64decode(str(value), validate=True)
    except ValueError:
        return None
