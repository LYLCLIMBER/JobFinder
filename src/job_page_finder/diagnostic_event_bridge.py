from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any

from job_page_finder.core_models import (
    ActionOutcome,
    AgentAction,
    Click,
    Complete,
    ObservationOptions,
    PageObservation,
    Scroll,
    Wait,
)
from job_page_finder.diagnostics import current_diagnostics
from job_page_finder.null_diagnostics import NullFinderEventSink
from job_page_finder.ports import ExplorationBrowser, FinderEventSink

logger = logging.getLogger(__name__)


def current_finder_event_sink() -> FinderEventSink:
    diagnostics = current_diagnostics()
    if diagnostics is None:
        return NullFinderEventSink()
    return FileFinderEventSink(diagnostics)


class FileFinderEventSink:
    def __init__(self, diagnostics: Any) -> None:
        self._diagnostics = diagnostics
        self._step_timeout = 30.0
        diagnostics.event("finder_started")

    @property
    def requires_image(self) -> bool:
        return bool(self._diagnostics.capture_screenshots)

    def step_started(self, step: int) -> None:
        self._diagnostics.event("step_started", step_id=_step_id(step), step=step)

    def page_observed(self, step: int, observation: PageObservation) -> None:
        step_id = _step_id(step)
        diagnostics = self._diagnostics
        diagnostics.event("page_observed", step_id=step_id, url=observation.url, title=observation.title)
        if diagnostics.is_diagnostic:
            diagnostics.artifact(
                "page_input",
                {"url": observation.url, "title": observation.title, "dom": observation.visible_text[:40_000]},
                event="page_input_saved",
                step_id=step_id,
            )
            diagnostics.screenshot(
                "screenshot",
                base64.b64encode(observation.screenshot).decode("ascii") if observation.screenshot else None,
                event="screenshot_saved",
                step_id=step_id,
            )
        if diagnostics.is_raw:
            diagnostics.artifact(
                "raw_page_snapshot",
                {
                    "url": observation.url,
                    "title": observation.title,
                    "dom": observation.visible_text,
                    "scroll_targets": [target.description for target in observation.scroll_targets],
                },
                event="raw_page_snapshot_saved",
                step_id=step_id,
            )
        if observation.screenshot is not None and observation.visual_candidates and diagnostics.is_diagnostic:
            diagnostics.screenshot(
                "annotated_screenshot",
                base64.b64encode(observation.screenshot).decode("ascii"),
                event="annotated_screenshot_saved",
                step_id=step_id,
            )
            diagnostics.artifact(
                "visual_candidates",
                [
                    {"ref": candidate.ref.value, "coordinates": list(candidate.box)}
                    for candidate in observation.visual_candidates
                ],
                event="visual_candidates_saved",
                step_id=step_id,
            )

    def decision_made(self, step: int, action: AgentAction) -> None:
        self._diagnostics.event(
            "action_selected",
            step_id=_step_id(step),
            action_id=_action_id(step),
            action=_action_name(action),
        )

    def action_finished(self, step: int, outcome: ActionOutcome) -> None:
        self._diagnostics.event(
            "action_finished",
            step_id=_step_id(step),
            action_id=_action_id(step),
            status="succeeded",
            summary=outcome.description[:2000],
        )

    def completion_resolved(self, step: int, *, accepted: bool) -> None:
        self._diagnostics.event(
            "action_finished",
            step_id=_step_id(step),
            action_id=_action_id(step),
            status="accepted" if accepted else "rejected",
        )
        if accepted:
            self._diagnostics.event("step_finished", step_id=_step_id(step), status="succeeded")

    def step_finished(
        self,
        step: int,
        *,
        status: str,
        error: str | None = None,
        action_started: bool = False,
    ) -> None:
        payload: dict[str, Any] = {"step_id": _step_id(step), "status": status}
        if error is not None:
            payload["error"] = error
        if action_started and status == "timed_out":
            self._diagnostics.event(
                "action_finished",
                step_id=_step_id(step),
                action_id=_action_id(step),
                status="timed_out",
            )
        elif action_started and status == "failed":
            self._diagnostics.event(
                "action_finished",
                step_id=_step_id(step),
                action_id=_action_id(step),
                status="failed",
            )
        self._diagnostics.event("step_finished", **payload)

    def action_timed_out(self, step: int) -> None:
        self._diagnostics.event(
            "action_finished",
            step_id=_step_id(step),
            action_id=_action_id(step),
            status="timed_out",
        )

    def browser_started(self) -> None:
        self._diagnostics.event("browser_started")

    def browser_navigated(self, url: str) -> None:
        self._diagnostics.event("browser_navigated", url=url)

    def browser_failed(self, error: str) -> None:
        self._diagnostics.event("browser_failed", error=error)

    def browser_cleanup_failed(self) -> None:
        self._diagnostics.event("browser_cleanup_failed")

    async def capture_raw_action_snapshot(self, browser: ExplorationBrowser, step: int) -> None:
        diagnostics = self._diagnostics
        if not diagnostics.is_raw:
            return
        step_id = _step_id(step)
        try:
            observation = await asyncio.wait_for(
                browser.observe(ObservationOptions(include_image=diagnostics.capture_screenshots)),
                timeout=min(self._step_timeout, 5),
            )
            diagnostics.artifact(
                "raw_action_snapshot",
                {
                    "url": observation.url,
                    "title": observation.title,
                    "dom": observation.visible_text,
                    "scroll_targets": [target.description for target in observation.scroll_targets],
                },
                event="raw_action_snapshot_saved",
                step_id=step_id,
                action_id=_action_id(step),
            )
            diagnostics.screenshot(
                "raw_action_screenshot",
                base64.b64encode(observation.screenshot).decode("ascii") if observation.screenshot else None,
                event="raw_action_screenshot_saved",
                step_id=step_id,
                action_id=_action_id(step),
            )
        except Exception as exc:
            diagnostics.event(
                "raw_action_snapshot_failed",
                step_id=step_id,
                action_id=_action_id(step),
                error=f"{type(exc).__name__}: {exc}",
            )


def _step_id(step: int) -> str:
    return f"step-{step}"


def _action_id(step: int) -> str:
    return f"{_step_id(step)}-action"


def _action_name(action: AgentAction) -> str:
    if isinstance(action, Click):
        return "click"
    if isinstance(action, Scroll):
        return "scroll"
    if isinstance(action, Wait):
        return "wait"
    if isinstance(action, Complete):
        return "done"
    return type(action).__name__.lower()
