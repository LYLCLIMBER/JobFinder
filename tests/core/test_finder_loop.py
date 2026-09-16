import asyncio
from collections.abc import Sequence

import pytest

from job_page_finder.contracts import FindJobPageRequest, FindJobPageSuccess
from job_page_finder.core_models import (
    ActionOutcome,
    AgentAction,
    Click,
    Complete,
    DecisionContext,
    ElementRef,
    ObservationOptions,
    PageObservation,
    Scroll,
    Wait,
)
from job_page_finder.finder import JobPageFinder
from job_page_finder.null_diagnostics import NullFinderEventSink
from job_page_finder.settings import FinderSettings


def observation(**kwargs: object) -> PageObservation:
    values = {
        "url": "https://example.com/",
        "title": "Example Company",
        "visible_text": "Senior Backend Engineer",
        "elements": (),
        "scroll_targets": (),
        "screenshot": None,
        "visual_candidates": (),
    }
    values.update(kwargs)
    return PageObservation(**values)  # type: ignore[arg-type]


class FakeBrowser:
    def __init__(self, outcomes: Sequence[object] | None = None) -> None:
        self.outcomes = list(outcomes or [])
        self.closed = False
        self.navigated_to: str | None = None
        self.observed_options: list[ObservationOptions] = []
        self.clicked: list[ElementRef] = []
        self.scrolled: list[tuple[ElementRef | None, str]] = []
        self.waits: list[int] = []

    async def navigate(self, url: str) -> None:
        self.navigated_to = url
        self._next()

    async def observe(self, options: ObservationOptions = ObservationOptions()) -> PageObservation:
        self.observed_options.append(options)
        value = self._next()
        return value if isinstance(value, PageObservation) else observation()

    async def click(self, element: ElementRef) -> ActionOutcome:
        self.clicked.append(element)
        return self._action()

    async def scroll(self, *, target: ElementRef | None, direction: str) -> ActionOutcome:
        self.scrolled.append((target, direction))
        return self._action()

    async def wait(self, seconds: int) -> ActionOutcome:
        self.waits.append(seconds)
        return self._action()

    async def close(self) -> None:
        self.closed = True
        self._next()

    def _action(self) -> ActionOutcome:
        value = self._next()
        if isinstance(value, ActionOutcome):
            return value
        return ActionOutcome(changed=True, current_url="https://example.com/jobs", description="changed")

    def _next(self) -> object | None:
        if not self.outcomes:
            return None
        value = self.outcomes.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


class FakeBrowserFactory:
    def __init__(self, browser: FakeBrowser | BaseException) -> None:
        self.browser = browser

    async def open(self) -> FakeBrowser:
        if isinstance(self.browser, BaseException):
            raise self.browser
        return self.browser


class FakeActionModel:
    def __init__(self, actions: Sequence[AgentAction | BaseException]) -> None:
        self.actions = list(actions)
        self.contexts: list[DecisionContext] = []

    async def decide(self, context: DecisionContext) -> AgentAction:
        self.contexts.append(context)
        action = self.actions.pop(0)
        if isinstance(action, BaseException):
            raise action
        return action


class ThrowingSink:
    @property
    def requires_image(self) -> bool:
        raise RuntimeError("image failed")

    def step_started(self, step: int) -> None:
        raise RuntimeError("step failed")

    def page_observed(self, step: int, observation: PageObservation) -> None:
        raise RuntimeError("observe failed")

    def decision_made(self, step: int, action: AgentAction) -> None:
        raise RuntimeError("decision failed")

    def action_finished(self, step: int, outcome: ActionOutcome) -> None:
        raise RuntimeError("action failed")


class SelfCancellingSink(ThrowingSink):
    @property
    def requires_image(self) -> bool:
        raise asyncio.CancelledError

    def step_started(self, step: int) -> None:
        raise asyncio.CancelledError

    def page_observed(self, step: int, observation: PageObservation) -> None:
        raise asyncio.CancelledError

    def decision_made(self, step: int, action: AgentAction) -> None:
        raise asyncio.CancelledError

    def action_finished(self, step: int, outcome: ActionOutcome) -> None:
        raise asyncio.CancelledError


def finder(
    browser: FakeBrowser | BaseException,
    actions: Sequence[AgentAction | BaseException],
    **settings: object,
) -> tuple[JobPageFinder, FakeActionModel]:
    model = FakeActionModel(actions)
    return (
        JobPageFinder(
            FakeBrowserFactory(browser),
            model,
            settings=FinderSettings(**settings),  # type: ignore[arg-type]
            events_factory=NullFinderEventSink,
        ),
        model,
    )


@pytest.mark.asyncio
async def test_completes_when_home_page_contains_a_job() -> None:
    browser = FakeBrowser([None, observation()])
    page_finder, _ = finder(
        browser,
        [Complete(job_title="Senior Backend Engineer", evidence_quote="Senior Backend Engineer")],
        use_vision=False,
    )

    result = await page_finder.find(FindJobPageRequest(company_url="https://example.com"))

    assert isinstance(result, FindJobPageSuccess)
    assert result.job_title == "Senior Backend Engineer"
    assert result.evidence.quote == "Senior Backend Engineer"
    assert result.steps == 1
    assert browser.navigated_to == "https://example.com/"
    assert browser.closed is True
    assert browser.observed_options[0].include_image is False


@pytest.mark.asyncio
async def test_click_scroll_and_wait_are_executed_then_complete() -> None:
    browser = FakeBrowser(
        [
            None,
            observation(visible_text="Careers"),
            ActionOutcome(changed=True, current_url="https://example.com/jobs", description="clicked"),
            observation(visible_text="Open roles"),
            ActionOutcome(changed=True, current_url="https://example.com/jobs", description="scrolled"),
            observation(visible_text="Open roles"),
            ActionOutcome(changed=False, current_url="https://example.com/jobs", description="waited"),
            observation(visible_text="Senior Backend Engineer"),
        ]
    )
    target = ElementRef("observation:1:index:7")
    page_finder, model = finder(
        browser,
        [
            Click(element=target),
            Scroll(direction="down", target=None),
            Wait(seconds=1),
            Complete(job_title="Senior Backend Engineer", evidence_quote="Senior Backend Engineer"),
        ],
    )

    result = await page_finder.find(FindJobPageRequest(company_url="https://example.com", max_steps=4))

    assert isinstance(result, FindJobPageSuccess)
    assert result.steps == 4
    assert browser.clicked == [target]
    assert browser.scrolled == [(None, "down")]
    assert browser.waits == [1]
    assert "clicked" in model.contexts[1].previous_outcome


@pytest.mark.asyncio
async def test_rejects_unverified_complete_then_accepts_feedback_follow_up() -> None:
    browser = FakeBrowser([None, observation(), observation()])
    page_finder, model = finder(
        browser,
        [
            Complete(job_title="Chief Astronaut", evidence_quote="Chief Astronaut"),
            Complete(job_title="Senior Backend Engineer", evidence_quote="Senior Backend Engineer"),
        ],
    )

    result = await page_finder.find(FindJobPageRequest(company_url="https://example.com"))

    assert isinstance(result, FindJobPageSuccess)
    assert result.steps == 2
    assert "done rejected: job_title does not occur in the current visible DOM" in model.contexts[1].previous_outcome


@pytest.mark.asyncio
async def test_classifies_model_action_validation_timeout_and_max_steps() -> None:
    model_error_browser = FakeBrowser([None, observation()])
    model_finder, _ = finder(model_error_browser, [RuntimeError("model failed")], max_consecutive_failures=1)
    model_result = await model_finder.find(FindJobPageRequest(company_url="https://example.com"))
    assert model_result.code == "MODEL_ERROR"
    assert model_error_browser.closed is True

    action_browser = FakeBrowser([None, observation(), RuntimeError("click failed")])
    action_finder, _ = finder(
        action_browser,
        [Click(element=ElementRef("observation:1:index:7"))],
        max_consecutive_failures=1,
    )
    action_result = await action_finder.find(FindJobPageRequest(company_url="https://example.com"))
    assert action_result.code == "ACTION_ERROR"

    validation_browser = FakeBrowser([None, observation(), observation()])
    validation_finder, _ = finder(
        validation_browser,
        [
            Complete(job_title="Chief Astronaut", evidence_quote="Chief Astronaut"),
            Complete(job_title="Chief Astronaut", evidence_quote="Chief Astronaut"),
        ],
        max_consecutive_failures=2,
    )
    validation_result = await validation_finder.find(FindJobPageRequest(company_url="https://example.com", max_steps=2))
    assert validation_result.code == "VALIDATION_FAILED"

    timeout_browser = FakeBrowser([None])

    async def slow_observe(options: ObservationOptions = ObservationOptions()) -> PageObservation:
        await asyncio.sleep(1)
        return observation()

    timeout_browser.observe = slow_observe  # type: ignore[method-assign]
    timeout_finder, _ = finder(timeout_browser, [], step_timeout=0.01, max_consecutive_failures=1)
    timeout_result = await timeout_finder.find(FindJobPageRequest(company_url="https://example.com"))
    assert timeout_result.code == "STEP_TIMEOUT"

    max_browser = FakeBrowser([None, observation(), observation()])
    max_finder, _ = finder(max_browser, [Wait(seconds=1), Wait(seconds=1)])
    max_result = await max_finder.find(FindJobPageRequest(company_url="https://example.com", max_steps=2))
    assert max_result.code == "MAX_STEPS_REACHED"
    assert max_browser.closed is True


@pytest.mark.asyncio
async def test_stale_reference_and_cleanup_paths() -> None:
    stale = FakeBrowser([None, observation(), ValueError("Element reference is not available")])
    stale_finder, _ = finder(
        stale,
        [Click(element=ElementRef("observation:0:index:7"))],
        max_consecutive_failures=1,
    )
    stale_result = await stale_finder.find(FindJobPageRequest(company_url="https://example.com"))
    assert stale_result.code == "ACTION_ERROR"
    assert stale.closed is True

    cleanup = FakeBrowser(
        [None, observation()],
    )
    original_close = cleanup.close

    async def failing_close() -> None:
        cleanup.closed = True
        raise RuntimeError("kill failed")

    cleanup.close = failing_close  # type: ignore[method-assign]
    cleanup_finder, _ = finder(
        cleanup,
        [Complete(job_title="Senior Backend Engineer", evidence_quote="Senior Backend Engineer")],
    )
    cleanup_result = await cleanup_finder.find(FindJobPageRequest(company_url="https://example.com"))
    assert isinstance(cleanup_result, FindJobPageSuccess)
    cleanup.close = original_close  # type: ignore[method-assign]

    blocking = FakeBrowser()
    observation_started = asyncio.Event()

    async def block_observe(options: ObservationOptions = ObservationOptions()) -> PageObservation:
        observation_started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    blocking.observe = block_observe  # type: ignore[method-assign]
    blocking_finder, _ = finder(blocking, [])
    task = asyncio.create_task(blocking_finder.find(FindJobPageRequest(company_url="https://example.com")))
    await observation_started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert blocking.closed is True


@pytest.mark.asyncio
async def test_throwing_finder_diagnostics_do_not_change_result() -> None:
    browser = FakeBrowser([None, observation()])
    page_finder, _ = finder(
        browser,
        [Complete(job_title="Senior Backend Engineer", evidence_quote="Senior Backend Engineer")],
    )

    result = await page_finder.find(FindJobPageRequest(company_url="https://example.com"), events=ThrowingSink())

    assert isinstance(result, FindJobPageSuccess)

    cancelling_finder, _ = finder(
        FakeBrowser([None, observation()]),
        [Complete(job_title="Senior Backend Engineer", evidence_quote="Senior Backend Engineer")],
    )
    self_cancelled = await cancelling_finder.find(
        FindJobPageRequest(company_url="https://example.com"), events=SelfCancellingSink()
    )
    assert isinstance(self_cancelled, FindJobPageSuccess)
