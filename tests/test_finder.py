import asyncio
import base64
import io
from dataclasses import dataclass
from typing import Any

import pytest
from browser_use.agent.views import ActionResult
from browser_use.llm.views import ChatInvokeCompletion
from PIL import Image

from job_page_finder import JobPageFinder, JobPageFinderInput
from job_page_finder.models import AgentDecision


class FakeDomState:
    def __init__(self, text: str, selector_map: dict[int, object] | None = None, *, has_root: bool = True) -> None:
        self._text = text
        self._root = object() if has_root else None
        self.selector_map = selector_map or {}

    def llm_representation(self) -> str:
        return self._text


@dataclass
class FakeState:
    url: str
    title: str
    dom_state: FakeDomState
    state_error: str | None = None
    page_info: Any = None
    screenshot: str | None = None


class FakeBrowser:
    def __init__(
        self,
        states: list[FakeState],
        *,
        start_error: Exception | None = None,
        start_delay: float = 0,
        navigate_delay: float = 0,
        kill_error: Exception | None = None,
        route_urls: list[str] | None = None,
    ) -> None:
        self.states = states
        self.position = 0
        self.start_error = start_error
        self.start_delay = start_delay
        self.navigate_delay = navigate_delay
        self.kill_error = kill_error
        self.route_urls = list(route_urls or [])
        self.current_url_calls = 0
        self.started = False
        self.killed = False
        self.navigated_to: str | None = None
        self.screenshot_options: list[bool] = []

    async def start(self) -> None:
        if self.start_delay:
            await asyncio.sleep(self.start_delay)
        if self.start_error:
            raise self.start_error
        self.started = True

    async def navigate_to(self, url: str) -> None:
        if self.navigate_delay:
            await asyncio.sleep(self.navigate_delay)
        self.navigated_to = url

    async def get_browser_state_summary(self, *, include_screenshot: bool) -> FakeState:
        self.screenshot_options.append(include_screenshot)
        return self.states[self.position]

    async def get_current_page_url(self) -> str:
        self.current_url_calls += 1
        if len(self.route_urls) > 1:
            return self.route_urls.pop(0)
        if self.route_urls:
            return self.route_urls[0]
        return self.states[self.position].url

    async def kill(self) -> None:
        self.killed = True
        if self.kill_error:
            raise self.kill_error

    def advance(self) -> None:
        self.position = min(self.position + 1, len(self.states) - 1)


class FakeTools:
    def __init__(self) -> None:
        self.clicks: list[int] = []
        self.scrolls: list[bool] = []
        self.scroll_indexes: list[int | None] = []

    async def click(self, *, index: int, browser_session: FakeBrowser) -> ActionResult:
        self.clicks.append(index)
        browser_session.advance()
        return ActionResult(extracted_content=f"Clicked element {index}")

    async def scroll(
        self, *, down: bool, pages: float, browser_session: FakeBrowser, index: int | None = None
    ) -> ActionResult:
        assert pages == 1.0
        self.scrolls.append(down)
        self.scroll_indexes.append(index)
        browser_session.advance()
        return ActionResult(extracted_content="Scrolled down" if down else "Scrolled up")


class FakeScrollNode:
    def __init__(self, offset: float) -> None:
        self.backend_node_id = 700
        self.tag_name = "main"
        self.is_visible = True
        self.is_actually_scrollable = True
        self.scroll_info = {
            "scroll_top": offset,
            "content_above": offset,
            "content_below": 200 - offset,
        }
        self.absolute_position = type("Rect", (), {"x": 0, "y": 0, "width": 200, "height": 100})()
        self.parent_node = None
        self.attributes = {}

    def get_meaningful_text_for_llm(self) -> str:
        return "Job list"


def scroll_state(*, root_offset: int, inner_offset: int) -> FakeState:
    node = FakeScrollNode(inner_offset)
    simplified = type("Simplified", (), {"original_node": node, "children": []})()
    dom_state = FakeDomState("[7]<main>Job list</main>", {7: node})
    dom_state._root = simplified
    return FakeState(
        url="https://example.com/",
        title="Example Company",
        dom_state=dom_state,
        page_info=type(
            "PageInfo",
            (),
            {
                "viewport_width": 200,
                "viewport_height": 100,
                "scroll_x": 0,
                "scroll_y": root_offset,
                "pixels_above": root_offset,
                "pixels_below": 0,
            },
        )(),
    )


class FakeLlm:
    model = "fake"

    def __init__(self, decisions: list[dict[str, Any] | Exception]) -> None:
        self.decisions = decisions
        self.calls: list[list[Any]] = []

    @property
    def provider(self) -> str:
        return "fake"

    @property
    def name(self) -> str:
        return self.model

    async def ainvoke(self, messages, output_format=None, **kwargs):
        self.calls.append(messages)
        decision = self.decisions.pop(0)
        if isinstance(decision, Exception):
            raise decision
        completion = AgentDecision.model_validate({"decision": decision})
        assert output_format is AgentDecision
        return ChatInvokeCompletion(completion=completion, usage=None)


def state(text: str, *, url: str = "https://example.com/", indexes: tuple[int, ...] = ()) -> FakeState:
    return FakeState(
        url=url,
        title="Example Company",
        dom_state=FakeDomState(text, {index: object() for index in indexes}),
    )


def make_finder(browser: FakeBrowser, llm: FakeLlm, **kwargs) -> tuple[JobPageFinder, FakeTools]:
    tools = FakeTools()
    kwargs.setdefault("scroll_route_timeout", 0.01)
    kwargs.setdefault("scroll_route_poll_interval", 0.001)
    finder = JobPageFinder(
        llm=llm,
        browser_factory=lambda: browser,
        tools=tools,
        **kwargs,
    )
    return finder, tools


@pytest.mark.asyncio
async def test_completes_when_home_page_contains_a_job() -> None:
    """Return the verified job from the initial page and clean up the browser."""
    browser = FakeBrowser([state("<h1>Open roles</h1><div>Senior Backend Engineer</div>")])
    llm = FakeLlm(
        [
            {
                "type": "done",
                "job_title": "Senior Backend Engineer",
                "evidence": "Senior Backend Engineer",
            }
        ]
    )
    finder, _ = make_finder(browser, llm, use_vision=False)

    result = await finder.find(JobPageFinderInput(company_url="https://example.com"))

    assert result.success is True
    assert result.job_page_url == "https://example.com/"
    assert result.job_title == "Senior Backend Engineer"
    assert result.evidence == "Senior Backend Engineer"
    assert result.steps == 1
    assert browser.started is True
    assert browser.navigated_to == "https://example.com/"
    assert browser.killed is True
    assert browser.screenshot_options == [False]


@pytest.mark.asyncio
async def test_sends_annotated_screenshot_when_textless_candidate_exists() -> None:
    node = type(
        "Node",
        (),
        {
            "absolute_position": type("Rect", (), {"x": 20, "y": 20, "width": 100, "height": 50})(),
            "is_visible": True,
            "ax_node": None,
            "children": [],
            "get_meaningful_text_for_llm": lambda self: "",
        },
    )()
    image = Image.new("RGB", (200, 100), "white")
    output = io.BytesIO()
    image.save(output, format="PNG")
    screenshot = base64.b64encode(output.getvalue()).decode("ascii")
    browser = FakeBrowser(
        [
            FakeState(
                url="https://example.com/",
                title="Example Company",
                dom_state=FakeDomState("[18]<div />", {18: node}),
                page_info=type(
                    "PageInfo",
                    (),
                    {
                        "viewport_width": 200,
                        "viewport_height": 100,
                        "scroll_x": 0,
                        "scroll_y": 0,
                        "pixels_above": 0,
                        "pixels_below": 0,
                    },
                )(),
                screenshot=screenshot,
            )
        ]
    )
    llm = FakeLlm([{"type": "wait", "seconds": 1}])
    finder, _ = make_finder(browser, llm, use_vision=True, max_consecutive_failures=1)

    result = await finder.find(JobPageFinderInput(company_url="https://example.com", max_steps=1))

    assert result.success is False
    assert result.error_code == "MAX_STEPS_REACHED"
    assert browser.screenshot_options == [True]
    assert len(llm.calls) == 1
    assert llm.calls[0][-1].content[1].text.startswith("Current browser screenshot")
    assert llm.calls[0][-1].content[2].type == "image_url"


@pytest.mark.asyncio
async def test_rejects_unverified_done_and_returns_feedback_to_model() -> None:
    """Reject an unverified job and expose the reason in the next model prompt."""
    browser = FakeBrowser([state("<div>Senior Backend Engineer</div>")])
    llm = FakeLlm(
        [
            {"type": "done", "job_title": "Chief Astronaut", "evidence": "Chief Astronaut"},
            {
                "type": "done",
                "job_title": "Senior Backend Engineer",
                "evidence": "Senior Backend Engineer",
            },
        ]
    )
    finder, _ = make_finder(browser, llm)

    result = await finder.find(JobPageFinderInput(company_url="https://example.com"))

    assert result.success is True
    assert result.job_title == "Senior Backend Engineer"
    assert result.evidence == "Senior Backend Engineer"
    assert result.steps == 2
    assert "done rejected: job_title does not occur in the current visible DOM" in llm.calls[1][-1].text


@pytest.mark.asyncio
async def test_fails_at_max_steps_when_no_specific_job_is_found() -> None:
    """Stop after the configured number of steps when no job is found."""
    browser = FakeBrowser([state("<h1>Careers at Example</h1>")])
    llm = FakeLlm(
        [
            {"type": "wait", "seconds": 1},
            {"type": "wait", "seconds": 1},
        ]
    )
    finder, _ = make_finder(browser, llm)

    result = await finder.find(JobPageFinderInput(company_url="https://example.com", max_steps=2))

    assert result.success is False
    assert result.steps == 2
    assert result.error == "Maximum steps reached without finding a specific job"
    assert result.error_code == "MAX_STEPS_REACHED"
    assert len(llm.calls) == 2
    assert browser.killed is True


@pytest.mark.asyncio
async def test_root_scroll_falls_back_to_internal_target_and_reports_actual_distance() -> None:
    browser = FakeBrowser(
        [
            scroll_state(root_offset=0, inner_offset=0),
            scroll_state(root_offset=0, inner_offset=0),
            scroll_state(root_offset=0, inner_offset=100),
        ]
    )
    llm = FakeLlm([{"type": "scroll", "direction": "down"}, {"type": "wait", "seconds": 1}])
    finder, tools = make_finder(browser, llm)

    result = await finder.find(JobPageFinderInput(company_url="https://example.com", max_steps=2))

    assert result.error_code == "MAX_STEPS_REACHED"
    assert tools.scrolls == [True, True]
    assert tools.scroll_indexes == [None, 7]
    assert browser.current_url_calls > 0
    assert "Scrolled element [7] down by 100px" in llm.calls[1][-1].text


@pytest.mark.asyncio
async def test_root_gesture_that_moves_internal_target_does_not_scroll_twice() -> None:
    browser = FakeBrowser(
        [
            scroll_state(root_offset=0, inner_offset=0),
            scroll_state(root_offset=0, inner_offset=100),
        ]
    )
    llm = FakeLlm([{"type": "scroll", "direction": "down"}, {"type": "wait", "seconds": 1}])
    finder, tools = make_finder(browser, llm)

    result = await finder.find(JobPageFinderInput(company_url="https://example.com", max_steps=2))

    assert result.error_code == "MAX_STEPS_REACHED"
    assert tools.scroll_indexes == [None]
    assert browser.current_url_calls == 1
    assert "Scrolled element [7] down by 100px" in llm.calls[1][-1].text


@pytest.mark.asyncio
async def test_scroll_detects_delayed_route_change_without_internal_fallback() -> None:
    browser = FakeBrowser(
        [
            scroll_state(root_offset=0, inner_offset=0),
            scroll_state(root_offset=0, inner_offset=0),
        ],
        route_urls=[
            "https://example.com/",
            "https://example.com/",
            "https://example.com/?page=product",
        ],
    )
    llm = FakeLlm([{"type": "scroll", "direction": "down"}, {"type": "wait", "seconds": 1}])
    finder, tools = make_finder(browser, llm, scroll_route_timeout=0.1, use_vision=False)

    result = await finder.find(JobPageFinderInput(company_url="https://example.com", max_steps=2))

    assert result.error_code == "MAX_STEPS_REACHED"
    assert tools.scroll_indexes == [None]
    assert browser.current_url_calls == 3
    assert "Wheel gesture changed route to https://example.com/?page=product" in llm.calls[1][-1].text
    assert browser.screenshot_options == [False, False, False]


@pytest.mark.asyncio
async def test_scroll_uses_live_url_as_route_baseline() -> None:
    browser = FakeBrowser(
        [
            scroll_state(root_offset=0, inner_offset=0),
            scroll_state(root_offset=0, inner_offset=0),
            scroll_state(root_offset=0, inner_offset=100),
        ],
        route_urls=["https://example.com/?page=product"],
    )
    llm = FakeLlm([{"type": "scroll", "direction": "down"}, {"type": "wait", "seconds": 1}])
    finder, tools = make_finder(browser, llm)

    result = await finder.find(JobPageFinderInput(company_url="https://example.com", max_steps=2))

    assert result.error_code == "MAX_STEPS_REACHED"
    assert tools.scroll_indexes == [None, 7]
    assert "Scrolled element [7] down by 100px" in llm.calls[1][-1].text


@pytest.mark.asyncio
async def test_scroll_without_actual_offset_change_is_an_action_error() -> None:
    browser = FakeBrowser([scroll_state(root_offset=0, inner_offset=0)])
    llm = FakeLlm([{"type": "scroll", "direction": "down", "index": 7}])
    finder, tools = make_finder(browser, llm, max_consecutive_failures=1)

    result = await finder.find(JobPageFinderInput(company_url="https://example.com", max_steps=1))

    assert result.error_code == "ACTION_ERROR"
    assert result.error == "Action failed: Scroll had no effect on the root page or available scroll targets"
    assert tools.scroll_indexes == [7]
    assert browser.current_url_calls == 0


@pytest.mark.asyncio
async def test_scroll_route_polling_stops_at_step_deadline() -> None:
    browser = FakeBrowser(
        [state("<div>Careers</div>")],
    )
    llm = FakeLlm([{"type": "scroll", "direction": "down"}])
    finder, tools = make_finder(
        browser,
        llm,
        max_consecutive_failures=1,
        step_timeout=0.1,
        scroll_route_timeout=1,
        scroll_route_poll_interval=0.01,
    )

    result = await finder.find(JobPageFinderInput(company_url="https://example.com", max_steps=1))

    assert result.error_code == "STEP_TIMEOUT"
    assert tools.scroll_indexes == [None]
    assert browser.current_url_calls > 1


@pytest.mark.asyncio
async def test_explicit_unavailable_scroll_target_is_rejected() -> None:
    browser = FakeBrowser([state("<div>Careers</div>")])
    llm = FakeLlm([{"type": "scroll", "direction": "down", "index": 99}])
    finder, _ = make_finder(browser, llm, max_consecutive_failures=1)

    result = await finder.find(JobPageFinderInput(company_url="https://example.com", max_steps=1))

    assert result.error_code == "ACTION_ERROR"
    assert "Scroll target index 99 is not available" in result.error


@pytest.mark.asyncio
async def test_rejects_an_index_from_an_old_browser_state() -> None:
    """Reject an action that references an element absent from the current DOM."""
    browser = FakeBrowser(
        [
            state("[7]<a>Careers</a>", indexes=(7,)),
            state("[9]<button>View openings</button>", indexes=(9,)),
        ]
    )
    llm = FakeLlm([{"type": "click", "index": 7}, {"type": "click", "index": 7}])
    finder, tools = make_finder(browser, llm, max_consecutive_failures=1)

    result = await finder.find(JobPageFinderInput(company_url="https://example.com", max_steps=2))

    assert result.success is False
    assert result.steps == 2
    assert result.error_code == "ACTION_ERROR"
    assert "not available in the current browser state" in result.error
    assert tools.clicks == [7]
    assert browser.killed is True


@pytest.mark.asyncio
async def test_kills_browser_when_startup_fails() -> None:
    """Return a startup error while still cleaning up the browser session."""
    browser = FakeBrowser([state("unused")], start_error=RuntimeError("cannot launch"))
    llm = FakeLlm([])
    finder, _ = make_finder(browser, llm)

    result = await finder.find(JobPageFinderInput(company_url="https://example.com"))

    assert result.success is False
    assert result.steps == 0
    assert result.error == "Browser initialization failed: cannot launch"
    assert result.error_code == "BROWSER_INITIALIZATION_FAILED"
    assert browser.killed is True


@pytest.mark.asyncio
async def test_enforces_step_timeout() -> None:
    """Fail a step that exceeds its timeout and clean up the browser session."""
    browser = FakeBrowser([state("<h1>Careers</h1>")])
    llm = FakeLlm([{"type": "wait", "seconds": 1}])
    finder, _ = make_finder(browser, llm, step_timeout=0.01, max_consecutive_failures=1)

    result = await finder.find(JobPageFinderInput(company_url="https://example.com"))

    assert result.success is False
    assert result.steps == 1
    assert result.error == "Step timed out after 0.01 seconds"
    assert result.error_code == "STEP_TIMEOUT"
    assert browser.killed is True


@pytest.mark.parametrize("timeout_phase", ["start", "navigate"])
@pytest.mark.asyncio
async def test_enforces_browser_initialization_timeout(timeout_phase: str) -> None:
    """Fail and clean up when browser startup or initial navigation times out."""
    browser = FakeBrowser(
        [state("unused")],
        start_delay=1 if timeout_phase == "start" else 0,
        navigate_delay=1 if timeout_phase == "navigate" else 0,
    )
    llm = FakeLlm([])
    finder, _ = make_finder(browser, llm, startup_timeout=0.01)

    result = await finder.find(JobPageFinderInput(company_url="https://example.com"))

    assert result.success is False
    assert result.steps == 0
    assert result.error == "Browser initialization timed out after 0.01 seconds"
    assert result.error_code == "BROWSER_INITIALIZATION_TIMEOUT"
    assert browser.killed is True


@pytest.mark.asyncio
async def test_classifies_model_errors_from_the_llm_path() -> None:
    """Classify LLM exceptions as MODEL_ERROR even when the message matches another failure."""
    browser = FakeBrowser([state("<h1>Careers</h1>")])
    llm = FakeLlm([RuntimeError("Maximum steps reached without finding a specific job")])
    finder, _ = make_finder(browser, llm, max_consecutive_failures=1)

    result = await finder.find(JobPageFinderInput(company_url="https://example.com"))

    assert result.success is False
    assert result.error_code == "MODEL_ERROR"
    assert result.error == "Action failed: Maximum steps reached without finding a specific job"
    assert browser.killed is True


@pytest.mark.asyncio
async def test_classifies_validation_failures_from_done_rejection() -> None:
    """Classify consecutive rejected done actions as VALIDATION_FAILED."""
    browser = FakeBrowser([state("<div>Senior Backend Engineer</div>")])
    llm = FakeLlm(
        [
            {"type": "done", "job_title": "Chief Astronaut", "evidence": "Chief Astronaut"},
            {"type": "done", "job_title": "Chief Astronaut", "evidence": "Chief Astronaut"},
        ]
    )
    finder, _ = make_finder(browser, llm, max_consecutive_failures=2)

    result = await finder.find(JobPageFinderInput(company_url="https://example.com", max_steps=2))

    assert result.success is False
    assert result.error_code == "VALIDATION_FAILED"
    assert "done rejected" in result.error
    assert browser.killed is True


@pytest.mark.asyncio
async def test_cleanup_exception_does_not_override_result() -> None:
    """Keep the original task result when browser cleanup fails."""
    browser = FakeBrowser(
        [state("<h1>Open roles</h1><div>Senior Backend Engineer</div>")],
        kill_error=RuntimeError("kill failed"),
    )
    llm = FakeLlm(
        [
            {
                "type": "done",
                "job_title": "Senior Backend Engineer",
                "evidence": "Senior Backend Engineer",
            }
        ]
    )
    finder, _ = make_finder(browser, llm)

    result = await finder.find(JobPageFinderInput(company_url="https://example.com"))

    assert result.success is True
    assert result.job_title == "Senior Backend Engineer"
    assert result.error is None
    assert browser.killed is True
