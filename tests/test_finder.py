import asyncio
from dataclasses import dataclass
from typing import Any

import pytest
from browser_use.agent.views import ActionResult
from browser_use.llm.views import ChatInvokeCompletion

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


class FakeBrowser:
    def __init__(
        self,
        states: list[FakeState],
        *,
        start_error: Exception | None = None,
        start_delay: float = 0,
        navigate_delay: float = 0,
    ) -> None:
        self.states = states
        self.position = 0
        self.start_error = start_error
        self.start_delay = start_delay
        self.navigate_delay = navigate_delay
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

    async def kill(self) -> None:
        self.killed = True

    def advance(self) -> None:
        self.position = min(self.position + 1, len(self.states) - 1)


class FakeTools:
    def __init__(self) -> None:
        self.clicks: list[int] = []
        self.scrolls: list[bool] = []

    async def click(self, *, index: int, browser_session: FakeBrowser) -> ActionResult:
        self.clicks.append(index)
        browser_session.advance()
        return ActionResult(extracted_content=f"Clicked element {index}")

    async def scroll(self, *, down: bool, pages: float, browser_session: FakeBrowser) -> ActionResult:
        assert pages == 1.0
        self.scrolls.append(down)
        browser_session.advance()
        return ActionResult(extracted_content="Scrolled down" if down else "Scrolled up")


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
    finder, _ = make_finder(browser, llm)

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
            {"type": "scroll", "direction": "down"},
            {"type": "scroll", "direction": "up"},
        ]
    )
    finder, _ = make_finder(browser, llm)

    result = await finder.find(JobPageFinderInput(company_url="https://example.com", max_steps=2))

    assert result.success is False
    assert result.steps == 2
    assert result.error == "Maximum steps reached without finding a specific job"
    assert len(llm.calls) == 2
    assert browser.killed is True


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
    assert browser.killed is True
