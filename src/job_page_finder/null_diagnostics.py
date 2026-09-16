from job_page_finder.core_models import ActionOutcome, AgentAction, PageObservation
from job_page_finder.task_protocol import TaskResult


class NullFinderEventSink:
    @property
    def requires_image(self) -> bool:
        return False

    def step_started(self, step: int) -> None:
        pass

    def page_observed(self, step: int, observation: PageObservation) -> None:
        pass

    def decision_made(self, step: int, action: AgentAction) -> None:
        pass

    def action_finished(self, step: int, outcome: ActionOutcome) -> None:
        pass


class NullRunDiagnostics:
    def __init__(self) -> None:
        self._finder_events = NullFinderEventSink()

    @property
    def finder_events(self) -> NullFinderEventSink:
        return self._finder_events

    def task_started(self) -> None:
        pass

    def task_finished(self, result: TaskResult) -> None:
        pass

    def abort(self) -> None:
        pass


class NullDiagnosticsFactory:
    def create(self, *, task_id: str, task_type: str) -> NullRunDiagnostics:
        return NullRunDiagnostics()
