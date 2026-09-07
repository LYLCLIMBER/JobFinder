from types import SimpleNamespace

from job_page_finder.scrolling import discover_scroll_targets, targets_for_direction


class Node:
    def __init__(
        self,
        backend_node_id: int,
        *,
        above: int = 0,
        below: int = 100,
        visible: bool = True,
        parent=None,
    ) -> None:
        self.backend_node_id = backend_node_id
        self.tag_name = "div"
        self.is_visible = visible
        self.is_actually_scrollable = True
        self.scroll_info = {
            "scroll_top": above,
            "content_above": above,
            "content_below": below,
        }
        self.absolute_position = SimpleNamespace(x=0, y=0, width=300, height=200)
        self.parent_node = parent
        self.attributes = {}

    def get_meaningful_text_for_llm(self) -> str:
        return "Scrollable content"


def state(nodes: list[Node], selector_map=None):
    root = SimpleNamespace(
        original_node=SimpleNamespace(
            tag_name="document",
            is_visible=True,
            is_actually_scrollable=False,
        ),
        children=[SimpleNamespace(original_node=node, children=[]) for node in nodes],
    )
    return SimpleNamespace(
        dom_state=SimpleNamespace(_root=root, selector_map=selector_map or {}),
        page_info=SimpleNamespace(
            viewport_width=300,
            viewport_height=200,
            scroll_x=0,
            scroll_y=0,
            pixels_above=0,
            pixels_below=0,
        ),
    )


def test_discovers_visible_targets_and_filters_by_direction() -> None:
    down_only = Node(1, below=100)
    up_only = Node(2, above=50, below=0)
    invisible = Node(3, visible=False)
    no_space = Node(4, above=0, below=0)

    targets = discover_scroll_targets(state([down_only, up_only, invisible, no_space]))

    assert [target.backend_node_id for target in targets] == [1, 2]
    assert [target.backend_node_id for target in targets_for_direction(targets, "down")] == [1]
    assert [target.backend_node_id for target in targets_for_direction(targets, "up")] == [2]


def test_active_modal_targets_are_listed_first() -> None:
    modal = SimpleNamespace(
        tag_name="div",
        attributes={"role": "dialog", "aria-modal": "true"},
        is_visible=True,
        parent_node=None,
    )
    ordinary = Node(1)
    modal_target = Node(2, parent=modal)

    targets = discover_scroll_targets(state([ordinary, modal_target]))

    assert [target.backend_node_id for target in targets] == [2, 1]
    assert targets[0].in_active_modal is True


def test_reuses_existing_index_and_allocates_after_selector_map() -> None:
    existing_target = Node(1)
    new_target = Node(2)
    unrelated = SimpleNamespace(backend_node_id=99)

    targets = discover_scroll_targets(state([existing_target, new_target], {5: existing_target, 12: unrelated}))

    assert [(target.backend_node_id, target.index) for target in targets] == [(1, 5), (2, 13)]
