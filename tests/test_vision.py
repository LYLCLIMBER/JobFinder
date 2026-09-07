import base64
import io
from types import SimpleNamespace

from PIL import Image

from job_page_finder.scrolling import ScrollTarget
from job_page_finder.vision import build_visual_context


class FakeNode:
    def __init__(self, *, text: str = "", attributes: dict[str, str] | None = None, bounds=None, ax_node=None) -> None:
        self.attributes = attributes or {}
        self.absolute_position = bounds
        self.ax_node = ax_node
        self.is_visible = True
        self.children = []
        self._text = text

    def get_meaningful_text_for_llm(self) -> str:
        return self._text


def screenshot_data_url(width: int = 400, height: int = 200) -> str:
    image = Image.new("RGB", (width, height), "white")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return base64.b64encode(output.getvalue()).decode("ascii")


def make_state(selector_map: dict[int, FakeNode], *, screenshot: str | None = None, scroll_y: int = 0):
    return SimpleNamespace(
        screenshot=screenshot,
        page_info=SimpleNamespace(
            viewport_width=400,
            viewport_height=200,
            scroll_x=0,
            scroll_y=scroll_y,
        ),
        dom_state=SimpleNamespace(selector_map=selector_map),
    )


def test_build_visual_context_marks_textless_visible_elements() -> None:
    state = make_state(
        {
            18: FakeNode(bounds=SimpleNamespace(x=40, y=60, width=120, height=50)),
            19: FakeNode(text="Careers", bounds=SimpleNamespace(x=220, y=60, width=120, height=50)),
        },
        screenshot=screenshot_data_url(),
    )

    context = build_visual_context(state)

    assert context is not None
    assert context.annotated_indexes == (18,)
    assert context.image_data_url.startswith("data:image/png;base64,")


def test_build_visual_context_excludes_accessible_and_invalid_elements() -> None:
    state = make_state(
        {
            1: FakeNode(
                attributes={"aria-label": "Menu"},
                bounds=SimpleNamespace(x=10, y=10, width=30, height=30),
            ),
            2: FakeNode(
                ax_node=SimpleNamespace(name="Search", description=None),
                bounds=SimpleNamespace(x=50, y=10, width=30, height=30),
            ),
            3: FakeNode(bounds=None),
            4: FakeNode(bounds=SimpleNamespace(x=10, y=250, width=30, height=30)),
        },
        screenshot=screenshot_data_url(),
    )

    assert build_visual_context(state) is None


def test_build_visual_context_excludes_descendant_image_labels() -> None:
    node = FakeNode(bounds=SimpleNamespace(x=10, y=10, width=100, height=50))
    node.children = [FakeNode(attributes={"alt": "Open positions"})]

    assert build_visual_context(make_state({1: node}, screenshot=screenshot_data_url())) is None


def test_build_visual_context_accounts_for_scroll_position() -> None:
    state = make_state(
        {18: FakeNode(bounds=SimpleNamespace(x=40, y=260, width=120, height=50))},
        screenshot=screenshot_data_url(),
        scroll_y=200,
    )

    context = build_visual_context(state)

    assert context is not None
    assert context.annotated_indexes == (18,)


def test_build_visual_context_marks_scroll_target_even_when_it_has_text() -> None:
    node = FakeNode(text="Job list", bounds=SimpleNamespace(x=20, y=20, width=300, height=150))
    target = ScrollTarget(
        index=25,
        node=node,
        offset=0,
        remaining_up=0,
        remaining_down=400,
        bounds=node.absolute_position,
    )

    context = build_visual_context(
        make_state({}, screenshot=screenshot_data_url()),
        scroll_targets=(target,),
    )

    assert context is not None
    assert context.annotated_indexes == (25,)
