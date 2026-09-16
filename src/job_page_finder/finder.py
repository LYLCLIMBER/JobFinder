from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from job_page_finder.completion import validate_completion
from job_page_finder.contracts import (
    RETRYABLE_FINDER_FAILURE_CODES,
    FindJobPageFailure,
    FindJobPageRequest,
    FindJobPageResult,
    FindJobPageSuccess,
)
from job_page_finder.core_models import (
    AgentAction,
    Click,
    Complete,
    DecisionContext,
    ObservationOptions,
    PageObservation,
    Scroll,
    Wait,
)
from job_page_finder.null_diagnostics import NullFinderEventSink
from job_page_finder.ports import ActionModel, BrowserFactory, ExplorationBrowser, FinderEventSink
from job_page_finder.settings import FinderSettings

logger = logging.getLogger(__name__)


class JobPageFinder:
    def __init__(
        self,
        browser_factory: BrowserFactory,
        action_model: ActionModel,
        *,
        settings: FinderSettings | None = None,
        events_factory: Callable[[], FinderEventSink] | None = None,
    ) -> None:
        self._browser_factory = browser_factory
        self._action_model = action_model
        self.settings = settings or FinderSettings()
        self._events_factory = events_factory or NullFinderEventSink

    @property
    def use_vision(self) -> bool:
        return self.settings.use_vision

    async def find(
        self,
        request: FindJobPageRequest,
        *,
        events: FinderEventSink | None = None,
    ) -> FindJobPageResult:
        sink = events or _create_sink(self._events_factory)
        try:
            if hasattr(sink, "_step_timeout"):
                sink._step_timeout = self.settings.step_timeout
        except Exception:
            logger.warning("Diagnostics sink setup failed", exc_info=True)
        return await self._find(request, events=sink)

    async def _find(self, request: FindJobPageRequest, *, events: FinderEventSink) -> FindJobPageResult:
        browser: ExplorationBrowser | None = None
        try:
            try:
                browser = await asyncio.wait_for(self._browser_factory.open(), timeout=self.settings.startup_timeout)
                _emit(events, "browser_started")
                await asyncio.wait_for(
                    browser.navigate(str(request.company_url)),
                    timeout=self.settings.startup_timeout,
                )
                _emit(events, "browser_navigated", str(request.company_url))
            except TimeoutError:
                return self._failure(
                    "BROWSER_INITIALIZATION_TIMEOUT",
                    f"Browser initialization timed out after {self.settings.startup_timeout:g} seconds",
                    steps=0,
                )
            except Exception as exc:
                _emit(events, "browser_failed", f"{type(exc).__name__}: {exc}")
                return self._failure(
                    "BROWSER_INITIALIZATION_FAILED",
                    f"Browser initialization failed: {exc}",
                    steps=0,
                )
            return await self._run_steps(browser, request, events)
        finally:
            if browser is not None:
                await close_browser_safely(browser, events)

    async def _run_steps(
        self,
        browser: ExplorationBrowser,
        request: FindJobPageRequest,
        events: FinderEventSink,
    ) -> FindJobPageResult:
        previous_result = "No previous action."
        consecutive_failures = 0
        last_error_code = "MAX_STEPS_REACHED"
        raw_snapshot = _raw_snapshot(events)

        for step in range(1, request.max_steps + 1):
            action_started = False
            try:
                _emit(events, "step_started", step)
                deadline = asyncio.get_running_loop().time() + self.settings.step_timeout
                include_image = self.settings.use_vision or _requires_image(events)
                observation = await _within_deadline(
                    browser.observe(ObservationOptions(include_image=include_image)),
                    deadline,
                )
                diagnostics_started = asyncio.get_running_loop().time()
                _emit(events, "page_observed", step, observation)
                deadline += asyncio.get_running_loop().time() - diagnostics_started
                try:
                    decision = await self._decide(
                        DecisionContext(
                            observation=_model_observation(observation, use_vision=self.settings.use_vision),
                            previous_outcome=previous_result,
                            step=step,
                            max_steps=request.max_steps,
                            company_url=str(request.company_url),
                        ),
                        deadline=deadline,
                    )
                except TimeoutError:
                    raise
                except Exception as exc:
                    previous_result = f"Action failed: {exc}"
                    consecutive_failures += 1
                    last_error_code = "MODEL_ERROR"
                else:
                    diagnostics_started = asyncio.get_running_loop().time()
                    _emit(events, "decision_made", step, decision)
                    deadline += asyncio.get_running_loop().time() - diagnostics_started
                    action_started = True
                    if isinstance(decision, Complete):
                        validated = validate_completion(decision, observation, step=step)
                        if isinstance(validated, FindJobPageSuccess):
                            _emit(events, "completion_resolved", step, accepted=True)
                            return validated
                        previous_result = f"done rejected: {validated.message}"
                        _emit(events, "completion_resolved", step, accepted=False)
                        consecutive_failures += 1
                        last_error_code = "VALIDATION_FAILED"
                    else:
                        outcome = await _within_deadline(
                            self._perform_action(browser, decision),
                            deadline,
                        )
                        previous_result = outcome.description
                        _emit(events, "action_finished", step, outcome)
                        if raw_snapshot is not None:
                            try:
                                await raw_snapshot(browser, step)
                            except asyncio.CancelledError:
                                if _caller_cancelled():
                                    raise
                                logger.warning("Diagnostics raw snapshot cancelled itself", exc_info=True)
                            except Exception:
                                logger.warning("Diagnostics raw snapshot failed", exc_info=True)
                        consecutive_failures = 0
            except TimeoutError:
                previous_result = f"Step timed out after {self.settings.step_timeout:g} seconds"
                consecutive_failures += 1
                last_error_code = "STEP_TIMEOUT"
                _emit(events, "step_finished", step, status="timed_out", action_started=action_started)
            except Exception as exc:
                previous_result = f"Action failed: {exc}"
                consecutive_failures += 1
                last_error_code = "ACTION_ERROR"
                _emit(
                    events,
                    "step_finished",
                    step,
                    status="failed",
                    error=f"{type(exc).__name__}: {exc}",
                    action_started=action_started,
                )

            if consecutive_failures == 0:
                _emit(events, "step_finished", step, status="completed")
            elif last_error_code in {"MODEL_ERROR", "VALIDATION_FAILED"}:
                _emit(events, "step_finished", step, status=last_error_code.lower())

            if consecutive_failures >= self.settings.max_consecutive_failures:
                return self._failure(last_error_code, previous_result, steps=step)

        return self._failure(
            "MAX_STEPS_REACHED",
            "Maximum steps reached without finding a specific job",
            steps=request.max_steps,
        )

    async def _decide(self, context: DecisionContext, *, deadline: float) -> AgentAction:
        decide = self._action_model.decide
        parameters = inspect.signature(type(self._action_model).decide).parameters
        if "deadline" in parameters:
            return await decide(context, deadline=deadline)
        return await _within_deadline(decide(context), deadline)

    async def _perform_action(self, browser: ExplorationBrowser, action: Click | Scroll | Wait) -> Any:
        if isinstance(action, Click):
            return await browser.click(action.element)
        if isinstance(action, Scroll):
            return await browser.scroll(target=action.target, direction=action.direction)
        return await browser.wait(action.seconds)

    @staticmethod
    def _failure(code: str, message: str, *, steps: int) -> FindJobPageFailure:
        return FindJobPageFailure(
            status="failed",
            code=code,  # type: ignore[arg-type]
            message=message,
            retryable=code in RETRYABLE_FINDER_FAILURE_CODES,
            steps=steps,
        )


async def close_browser_safely(browser: ExplorationBrowser, events: FinderEventSink | None = None) -> None:
    cleanup_task = asyncio.create_task(browser.close())
    try:
        await asyncio.shield(cleanup_task)
    except asyncio.CancelledError:
        current = asyncio.current_task()
        if current is not None and current.cancelling():
            try:
                await asyncio.shield(cleanup_task)
            except BaseException:
                logger.warning("Browser cleanup failed", exc_info=True)
                _emit(events, "browser_cleanup_failed")
            raise
        logger.warning("Browser cleanup cancelled itself")
        _emit(events, "browser_cleanup_failed")
    except Exception:
        logger.warning("Browser cleanup failed", exc_info=True)
        _emit(events, "browser_cleanup_failed")


def _model_observation(observation: PageObservation, *, use_vision: bool) -> PageObservation:
    if use_vision:
        return observation
    return replace(observation, screenshot=None, visual_candidates=())


def _emit(target: object | None, name: str, *args: Any, **kwargs: Any) -> None:
    if target is None:
        return
    try:
        method = getattr(target, name, None)
    except asyncio.CancelledError:
        if _caller_cancelled():
            raise
        logger.warning("Diagnostics event %s lookup cancelled itself", name, exc_info=True)
        return
    except Exception:
        logger.warning("Diagnostics event %s lookup failed", name, exc_info=True)
        return
    if callable(method):
        try:
            method(*args, **kwargs)
        except asyncio.CancelledError:
            if _caller_cancelled():
                raise
            logger.warning("Diagnostics event %s cancelled itself", name, exc_info=True)
        except Exception:
            logger.warning("Diagnostics event %s failed", name, exc_info=True)


def _create_sink(factory: Callable[[], FinderEventSink]) -> FinderEventSink:
    try:
        return factory()
    except asyncio.CancelledError:
        if _caller_cancelled():
            raise
        logger.warning("Diagnostics event sink creation cancelled itself", exc_info=True)
        return NullFinderEventSink()
    except Exception:
        logger.warning("Diagnostics event sink creation failed", exc_info=True)
        return NullFinderEventSink()


def _requires_image(events: FinderEventSink) -> bool:
    try:
        return events.requires_image
    except asyncio.CancelledError:
        if _caller_cancelled():
            raise
        logger.warning("Diagnostics image requirement check cancelled itself", exc_info=True)
        return False
    except Exception:
        logger.warning("Diagnostics image requirement check failed", exc_info=True)
        return False


def _raw_snapshot(events: FinderEventSink) -> Callable[..., Any] | None:
    try:
        snapshot = getattr(events, "capture_raw_action_snapshot", None)
    except asyncio.CancelledError:
        if _caller_cancelled():
            raise
        logger.warning("Diagnostics raw snapshot lookup cancelled itself", exc_info=True)
        return None
    except Exception:
        logger.warning("Diagnostics raw snapshot lookup failed", exc_info=True)
        return None
    return snapshot if callable(snapshot) else None


def _caller_cancelled() -> bool:
    current = asyncio.current_task()
    return current is not None and current.cancelling() > 0


async def _within_deadline(awaitable: Any, deadline: float) -> Any:
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        close = getattr(awaitable, "close", None)
        if close is not None:
            close()
        raise TimeoutError
    return await asyncio.wait_for(awaitable, timeout=remaining)
