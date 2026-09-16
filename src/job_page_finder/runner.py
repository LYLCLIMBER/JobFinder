import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from job_page_finder import task_protocol
from job_page_finder.adapters.diagnostics import as_run_diagnostics
from job_page_finder.application import TaskApplication
from job_page_finder.contracts import FindJobPageResult
from job_page_finder.diagnostics import DiagnosticWriter
from job_page_finder.finder import JobPageFinder
from job_page_finder.models import (
    JobPageFinderInput,
    JobPageFinderResult,
    from_legacy_finder_result,
    to_legacy_finder_request,
)
from job_page_finder.null_diagnostics import NullDiagnosticsFactory
from job_page_finder.ports import DiagnosticsFactory, RunDiagnostics
from job_page_finder.task_protocol import FindJobPageRequest, FindJobPageTaskOutput, TaskRequest, TaskResult

logger = logging.getLogger(__name__)

FindJobPageOutput = FindJobPageTaskOutput
RETRYABLE_ERROR_CODES = task_protocol.RETRYABLE_ERROR_CODES
SUPPORTED_TASK_TYPE = task_protocol.SUPPORTED_TASK_TYPE
SUPPORTED_TASK_VERSION = task_protocol.SUPPORTED_TASK_VERSION
TaskError = task_protocol.TaskError
TaskErrorCode = task_protocol.TaskErrorCode
TaskMetadata = task_protocol.TaskMetadata
build_failed_result = task_protocol.build_task_failure

FindExecutor = Callable[[JobPageFinderInput], Awaitable[JobPageFinderResult]]


def resolve_task_id(value: str | None) -> str:
    return task_protocol._resolve_task_id(value)


def request_identity(request: object) -> tuple[str, str]:
    return task_protocol._extract_identity(request)


def log_task_started(task_id: str, task_type: str) -> None:
    logger.info("task started task_id=%s task_type=%s", task_id, task_type)


def log_task_finished(result: TaskResult) -> None:
    error_code = result.error.code if result.error is not None else None
    logger.info(
        "task finished task_id=%s task_type=%s status=%s duration_ms=%s error_code=%s",
        result.task_id,
        result.type,
        result.status,
        result.metadata.duration_ms,
        error_code,
    )


def _exception_message(exc: BaseException) -> str:
    message = str(exc).strip()
    name = type(exc).__name__
    if not message:
        return name
    return f"{name}: {message}"


def _elapsed_ms(started: float) -> int:
    from job_page_finder.application import _elapsed_ms as elapsed

    return elapsed(started)


class _CallableFinder:
    def __init__(self, execute: FindExecutor) -> None:
        self._execute = execute

    async def find(
        self,
        request: JobPageFinderInput | FindJobPageRequest,
        *,
        events: object | None = None,
    ) -> FindJobPageResult:
        legacy_request = request if isinstance(request, JobPageFinderInput) else to_legacy_finder_request(request)
        legacy_result = await self._execute(legacy_request)
        return from_legacy_finder_result(legacy_result)


class _StaticFinderProvider:
    def __init__(self, finder: JobPageFinder | FindExecutor) -> None:
        self._finder = finder

    def get(self) -> JobPageFinder:
        if isinstance(self._finder, JobPageFinder):
            return self._finder
        return _CallableFinder(self._finder)  # type: ignore[return-value]


class _FixedDiagnosticsFactory:
    def __init__(self, run: RunDiagnostics) -> None:
        self._run = run

    def create(self, *, task_id: str, task_type: str) -> RunDiagnostics:
        return self._run


class TaskRunner:
    def __init__(
        self,
        finder: JobPageFinder | FindExecutor | None = None,
        *,
        application: TaskApplication | None = None,
    ) -> None:
        self._application = application
        if application is not None:
            self._finder_provider = None
        else:
            if finder is None:
                raise TypeError("TaskRunner requires a finder or application")
            self._finder_provider = _StaticFinderProvider(finder)

    async def run(
        self,
        request: TaskRequest | Mapping[str, Any],
        *,
        emit_started: bool = True,
        diagnostics: RunDiagnostics | DiagnosticWriter | None = None,
    ) -> TaskResult:
        if self._application is not None:
            return await self._application.run(request, emit_started=emit_started)
        application = TaskApplication(self._finder_provider, _factory_for(diagnostics))
        return await application.run(request, emit_started=emit_started)


def _factory_for(diagnostics: RunDiagnostics | DiagnosticWriter | None) -> DiagnosticsFactory:
    if diagnostics is None:
        return NullDiagnosticsFactory()
    return _FixedDiagnosticsFactory(as_run_diagnostics(diagnostics))
