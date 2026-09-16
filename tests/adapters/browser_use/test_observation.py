from types import SimpleNamespace

from job_page_finder.adapters.browser_use import observation as observation_module
from job_page_finder.adapters.browser_use.observation import build_observation
from job_page_finder.adapters.browser_use.vision import VisualSnapshot
from job_page_finder.core_models import ElementRef


class Node:
    tag_name = "a"
    attributes = {"role": "link", "href": "/careers"}
    backend_node_id = 70
    is_visible = True
    is_actually_scrollable = True
    scroll_info = {"scroll_top": 0, "content_above": 0, "content_below": 100}
    absolute_position = SimpleNamespace(x=0, y=0, width=100, height=100)
    parent_node = None

    def get_meaningful_text_for_llm(self) -> str:
        return "Careers"


def test_browser_state_maps_to_adapter_neutral_observation(monkeypatch) -> None:
    node = Node()
    root = SimpleNamespace(original_node=node, children=[])
    state = SimpleNamespace(
        url="https://example.com/",
        title="Example",
        screenshot="raw-image",
        dom_state=SimpleNamespace(
            selector_map={7: node},
            _root=root,
            llm_representation=lambda: "[7]<a>Careers</a>",
        ),
        page_info=SimpleNamespace(
            scroll_x=0,
            scroll_y=0,
            pixels_above=0,
            pixels_below=100,
            viewport_width=100,
            viewport_height=100,
        ),
    )
    monkeypatch.setattr(
        observation_module,
        "build_visual_snapshot",
        lambda *args, **kwargs: VisualSnapshot(
            screenshot=b"annotated",
            candidate_boxes={ElementRef("observation:0:index:7"): (1, 2, 3, 4)},
        ),
    )

    observation = build_observation(state, include_image=True).observation

    assert observation.url == "https://example.com/"
    assert observation.visible_text == "[7]<a>Careers</a>"
    assert observation.elements[0].ref.value == "observation:0:index:7"
    assert observation.elements[0].role == "link"
    assert observation.scroll_targets[0].ref is None
    assert observation.scroll_targets[1].can_scroll_down is True
    assert observation.screenshot == b"annotated"
    assert observation.visual_candidates[0].box == (1, 2, 3, 4)


def test_observation_preserves_dom_larger_than_model_limit() -> None:
    dom = "x" * 50_000
    state = SimpleNamespace(
        url="https://example.com/",
        title="Example",
        screenshot=None,
        dom_state=SimpleNamespace(selector_map={}, _root=object(), llm_representation=lambda: dom),
        page_info=SimpleNamespace(scroll_x=0, scroll_y=0, pixels_above=0, pixels_below=0),
    )

    observation = build_observation(state, include_image=False, max_dom_characters=1).observation

    assert observation.visible_text == dom
