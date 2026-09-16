import asyncio
from collections.abc import Sequence

import pytest

from job_page_finder.core_models import (
    ActionOutcome,
    AgentAction,
    Complete,
    DecisionContext,
    ElementRef,
    ObservationOptions,
    PageObservation,
)
from job_page_finder.null_diagnostics import NullDiagnosticsFactory
from job_page_finder.ports import (
    ActionModel,
    BrowserFactory,
    DiagnosticsFactory,
    ExplorationBrowser,
    FinderEventSink,
    RunDiagnostics,
)
from job_page_finder.task_protocol import build_task_failure


def observation() -> PageObservation:
    return PageObservation(
        url="https://example.com/jobs",
        title="Jobs",
        visible_text="Engineer",
        elements=(),
        scroll_targets=(),
        screenshot=None,
        visual_candidates=(),
    )


class FakeBrowser:
    def __init__(self, outcomes: Sequence[object] = ()) -> None:
        self.outcomes = list(outcomes)
        self.closed = False

    async def navigate(self, url: str) -> None:
        self._next()

    async def observe(self, options: ObservationOptions = ObservationOptions()) -> PageObservation:
        value = self._next()
        return value if isinstance(value, PageObservation) else observation()

    async def click(self, element: ElementRef) -> ActionOutcome:
        return self._action()

    async def scroll(self, *, target: ElementRef | None, direction: str) -> ActionOutcome:
        return self._action()

    async def wait(self, seconds: int) -> ActionOutcome:
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
    def __init__(self, action: AgentAction | BaseException) -> None:
        self.action = action

    async def decide(self, context: DecisionContext) -> AgentAction:
        if isinstance(self.action, BaseException):
            raise self.action
        return self.action


class LifecycleBrowser(FakeBrowser):
    def __init__(self, *, body: object = None, cleanup: object = None) -> None:
        super().__init__()
        self.body = body
        self.cleanup = cleanup
        self.body_started = asyncio.Event()
        self.cleanup_started = asyncio.Event()
        self.body_release = asyncio.Event()
        self.cleanup_release = asyncio.Event()

    async def run_body(self) -> str:
        self.body_started.set()
        if self.body == "block":
            await self.body_release.wait()
            return "released"
        if isinstance(self.body, BaseException):
            raise self.body
        return "success"

    async def close(self) -> None:
        self.closed = True
        self.cleanup_started.set()
        if self.cleanup == "block":
            await self.cleanup_release.wait()
        elif isinstance(self.cleanup, BaseException):
            raise self.cleanup


async def close_safely_for_contract(browser: LifecycleBrowser) -> None:
    cleanup_task = asyncio.create_task(browser.close())
    try:
        await asyncio.shield(cleanup_task)
    except asyncio.CancelledError:
        if asyncio.current_task().cancelling():
            try:
                await asyncio.shield(cleanup_task)
            except BaseException:
                pass
            raise
        # A cleanup task that cancels itself is a cleanup failure, not caller cancellation.
    except Exception:
        pass


async def run_minimal_lifecycle(browser: LifecycleBrowser) -> str:
    try:
        return await browser.run_body()
    finally:
        await close_safely_for_contract(browser)


def test_port_implementers_and_null_diagnostics_satisfy_protocols() -> None:
    browser = FakeBrowser()
    factory = FakeBrowserFactory(browser)
    model = FakeActionModel(Complete(job_title="Engineer", evidence_quote="Engineer"))
    diagnostics = NullDiagnosticsFactory()
    run = diagnostics.create(task_id="task", task_type="find_job_page")

    assert isinstance(browser, ExplorationBrowser)
    assert isinstance(factory, BrowserFactory)
    assert isinstance(model, ActionModel)
    assert isinstance(diagnostics, DiagnosticsFactory)
    assert isinstance(run, RunDiagnostics)
    assert isinstance(run.finder_events, FinderEventSink)
    assert run.finder_events.requires_image is False


@pytest.mark.asyncio
async def test_fake_ports_execute_a_minimal_finder_flow() -> None:
    expected_observation = observation()
    browser = FakeBrowser([None, expected_observation])
    factory = FakeBrowserFactory(browser)
    action = Complete(job_title="Engineer", evidence_quote="Engineer")
    model = FakeActionModel(action)
    run = NullDiagnosticsFactory().create(task_id="task", task_type="find_job_page")

    run.task_started()
    opened = await factory.open()
    await opened.navigate("https://example.com")
    current = await opened.observe(ObservationOptions(include_image=False))
    run.finder_events.step_started(1)
    run.finder_events.page_observed(1, current)
    decision = await model.decide(
        DecisionContext(
            observation=current,
            previous_outcome="",
            step=1,
            max_steps=1,
            company_url="https://example.com",
        )
    )
    run.finder_events.decision_made(1, decision)
    outcome = ActionOutcome(changed=False, current_url=current.url, description="completed")
    run.finder_events.action_finished(1, outcome)
    await opened.close()
    run.task_finished(
        build_task_failure(
            task_id="task",
            task_type="find_job_page",
            code="MAX_STEPS_REACHED",
            message="contract result",
            duration_ms=0,
        )
    )
    run.abort()

    assert decision == action
    assert browser.closed is True


@pytest.mark.asyncio
async def test_fake_ports_express_success_action_failure_and_timeout() -> None:
    browser = FakeBrowser([None, observation(), RuntimeError("action failed")])
    await browser.navigate("https://example.com")
    assert await browser.observe() == observation()
    with pytest.raises(RuntimeError, match="action failed"):
        await browser.click(ElementRef("index:1"))

    blocking_model = FakeActionModel(asyncio.CancelledError())
    context = DecisionContext(
        observation=observation(),
        previous_outcome="",
        step=1,
        max_steps=1,
        company_url="https://example.com",
    )
    with pytest.raises(asyncio.CancelledError):
        await blocking_model.decide(context)

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(asyncio.Event().wait(), timeout=0.001)


@pytest.mark.asyncio
async def test_fake_ports_distinguish_caller_cancellation_from_cleanup_cancellation() -> None:
    caller_browser = LifecycleBrowser(body="block", cleanup="block")
    caller_task = asyncio.create_task(run_minimal_lifecycle(caller_browser))
    await caller_browser.body_started.wait()
    caller_task.cancel()
    await caller_browser.cleanup_started.wait()
    assert caller_task.done() is False
    caller_browser.cleanup_release.set()
    with pytest.raises(asyncio.CancelledError):
        await caller_task
    assert caller_browser.closed is True

    cleanup_cancelled = LifecycleBrowser(cleanup=asyncio.CancelledError())
    assert await run_minimal_lifecycle(cleanup_cancelled) == "success"
    assert cleanup_cancelled.closed is True


@pytest.mark.asyncio
async def test_fake_ports_preserve_main_outcomes_across_cleanup_failures() -> None:
    successful = LifecycleBrowser(cleanup=RuntimeError("cleanup failed"))
    assert await run_minimal_lifecycle(successful) == "success"
    assert successful.closed is True

    main_error = RuntimeError("main failed")
    failed = LifecycleBrowser(body=main_error, cleanup=RuntimeError("cleanup failed"))
    with pytest.raises(RuntimeError, match="main failed"):
        await run_minimal_lifecycle(failed)
    assert failed.closed is True

    timed_out = LifecycleBrowser(body=TimeoutError("operation timed out"))
    with pytest.raises(TimeoutError, match="operation timed out"):
        await run_minimal_lifecycle(timed_out)
    assert timed_out.closed is True
