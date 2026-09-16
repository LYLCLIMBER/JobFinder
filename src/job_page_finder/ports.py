from typing import Literal, Protocol, runtime_checkable

from job_page_finder.core_models import (
    ActionOutcome,
    AgentAction,
    DecisionContext,
    ElementRef,
    ObservationOptions,
    PageObservation,
)
from job_page_finder.task_protocol import TaskResult


@runtime_checkable
class ExplorationBrowser(Protocol):
    async def navigate(self, url: str) -> None: ...

    async def observe(self, options: ObservationOptions = ObservationOptions()) -> PageObservation: ...

    async def click(self, element: ElementRef) -> ActionOutcome: ...

    async def scroll(
        self,
        *,
        target: ElementRef | None,
        direction: Literal["up", "down"],
    ) -> ActionOutcome: ...

    async def wait(self, seconds: int) -> ActionOutcome: ...

    async def close(self) -> None: ...


@runtime_checkable
class BrowserFactory(Protocol):
    """Owns partial resources until open succeeds; callers own the returned browser."""

    async def open(self) -> ExplorationBrowser: ...


@runtime_checkable
class ActionModel(Protocol):
    async def decide(self, context: DecisionContext) -> AgentAction: ...


@runtime_checkable
class FinderEventSink(Protocol):
    @property
    def requires_image(self) -> bool: ...

    def step_started(self, step: int) -> None: ...

    def page_observed(self, step: int, observation: PageObservation) -> None: ...

    def decision_made(self, step: int, action: AgentAction) -> None: ...

    def action_finished(self, step: int, outcome: ActionOutcome) -> None: ...


@runtime_checkable
class RunDiagnostics(Protocol):
    @property
    def finder_events(self) -> FinderEventSink: ...

    def task_started(self) -> None: ...

    def task_finished(self, result: TaskResult) -> None: ...

    def abort(self) -> None: ...


@runtime_checkable
class DiagnosticsFactory(Protocol):
    def create(self, *, task_id: str, task_type: str) -> RunDiagnostics: ...
