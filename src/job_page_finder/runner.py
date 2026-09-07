import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from job_page_finder.diagnostics import DiagnosticWriter, reset_current_diagnostics, set_current_diagnostics
from job_page_finder.finder import JobPageFinder
from job_page_finder.models import JobPageFinderInput, JobPageFinderResult

logger = logging.getLogger(__name__)

SUPPORTED_TASK_VERSION = "v1"
SUPPORTED_TASK_TYPE = "find_job_page"

TaskErrorCode = Literal[
    "INVALID_TASK",
    "UNSUPPORTED_TASK_TYPE",
    "CONFIGURATION_ERROR",
    "BROWSER_INITIALIZATION_FAILED",
    "BROWSER_INITIALIZATION_TIMEOUT",
    "STEP_TIMEOUT",
    "MODEL_ERROR",
    "ACTION_ERROR",
    "VALIDATION_FAILED",
    "MAX_STEPS_REACHED",
    "INTERNAL_ERROR",
]

RETRYABLE_ERROR_CODES: frozenset[str] = frozenset(
    {
        "BROWSER_INITIALIZATION_FAILED",
        "BROWSER_INITIALIZATION_TIMEOUT",
        "STEP_TIMEOUT",
        "MODEL_ERROR",
        "ACTION_ERROR",
        "INTERNAL_ERROR",
    }
)

FindExecutor = Callable[[JobPageFinderInput], Awaitable[JobPageFinderResult]]


class TaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal["v1"]
    task_id: str | None = None
    type: Literal["find_job_page"]
    payload: JobPageFinderInput


class _TaskRequestProbe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    task_id: str | None = None
    type: str
    payload: Any


class FindJobPageOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_page_url: str = Field(min_length=1)
    job_title: str = Field(min_length=1)
    evidence: str = Field(min_length=1)
    steps: int


class TaskError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: TaskErrorCode
    message: str
    retryable: bool


class TaskMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    duration_ms: int = Field(ge=0)


class TaskResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal["v1"]
    task_id: str
    type: str
    status: Literal["succeeded", "failed"]
    output: FindJobPageOutput | None = None
    error: TaskError | None = None
    metadata: TaskMetadata

    @model_validator(mode="after")
    def validate_status_payload(self) -> "TaskResult":
        if self.status == "succeeded":
            if self.output is None or self.error is not None:
                raise ValueError("succeeded result must have output and no error")
        elif self.output is not None or self.error is None:
            raise ValueError("failed result must have error and no output")
        return self


def resolve_task_id(value: str | None) -> str:
    if value is None or value == "":
        return uuid.uuid4().hex
    return value


def request_identity(request: object) -> tuple[str, str]:
    task_id: str | None = None
    task_type = "unknown"
    if isinstance(request, TaskRequest):
        task_id = request.task_id
        task_type = request.type
    elif isinstance(request, Mapping):
        raw_id = request.get("task_id")
        if isinstance(raw_id, str):
            task_id = raw_id
        raw_type = request.get("type")
        if isinstance(raw_type, str) and raw_type:
            task_type = raw_type
    return resolve_task_id(task_id), task_type


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


def build_failed_result(
    *,
    task_id: str,
    task_type: str,
    code: TaskErrorCode,
    message: str,
    duration_ms: int,
) -> TaskResult:
    return TaskResult(
        version="v1",
        task_id=task_id,
        type=task_type,
        status="failed",
        output=None,
        error=TaskError(code=code, message=message, retryable=code in RETRYABLE_ERROR_CODES),
        metadata=TaskMetadata(duration_ms=max(duration_ms, 0)),
    )


def _validation_message(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "Invalid task request"
    err = errors[0]
    loc = ".".join(str(part) for part in err.get("loc", ()))
    msg = err.get("msg", "invalid")
    return f"Invalid task request: {loc}: {msg}" if loc else f"Invalid task request: {msg}"


def _exception_message(exc: BaseException) -> str:
    message = str(exc).strip()
    name = type(exc).__name__
    if not message:
        return name
    return f"{name}: {message}"


class TaskRunner:
    def __init__(self, finder: JobPageFinder | FindExecutor) -> None:
        if isinstance(finder, JobPageFinder):
            self._execute_find = finder.find
        else:
            self._execute_find = finder

    async def run(
        self,
        request: TaskRequest | Mapping[str, Any],
        *,
        emit_started: bool = True,
        diagnostics: DiagnosticWriter | None = None,
    ) -> TaskResult:
        started = time.perf_counter()
        task_id, task_type = request_identity(request)
        token = set_current_diagnostics(diagnostics)
        try:
            if diagnostics is not None:
                diagnostics.event("task_started")
            if emit_started:
                log_task_started(task_id, task_type)
            try:
                parsed = self.parse_request(request)
                task_type = parsed.type
                result = await self._execute_find(parsed.payload)
                duration_ms = _elapsed_ms(started)
                if result.success:
                    if not (result.job_page_url and result.job_title and result.evidence):
                        task_result = build_failed_result(
                            task_id=task_id,
                            task_type=task_type,
                            code="INTERNAL_ERROR",
                            message="Finder returned a successful result without job page fields",
                            duration_ms=duration_ms,
                        )
                    else:
                        task_result = TaskResult(
                            version="v1",
                            task_id=task_id,
                            type=task_type,
                            status="succeeded",
                            output=FindJobPageOutput(
                                job_page_url=result.job_page_url,
                                job_title=result.job_title,
                                evidence=result.evidence,
                                steps=result.steps,
                            ),
                            error=None,
                            metadata=TaskMetadata(duration_ms=duration_ms),
                        )
                else:
                    code: TaskErrorCode = result.error_code or "INTERNAL_ERROR"
                    task_result = build_failed_result(
                        task_id=task_id,
                        task_type=task_type,
                        code=code,
                        message=result.error or "Task failed",
                        duration_ms=duration_ms,
                    )
            except _UnsupportedTaskTypeError as exc:
                task_result = build_failed_result(
                    task_id=task_id,
                    task_type=exc.task_type,
                    code="UNSUPPORTED_TASK_TYPE",
                    message=str(exc),
                    duration_ms=_elapsed_ms(started),
                )
            except _InvalidTaskError as exc:
                task_result = build_failed_result(
                    task_id=task_id,
                    task_type=task_type,
                    code="INVALID_TASK",
                    message=str(exc),
                    duration_ms=_elapsed_ms(started),
                )
            except Exception as exc:
                task_result = build_failed_result(
                    task_id=task_id,
                    task_type=task_type,
                    code="INTERNAL_ERROR",
                    message=_exception_message(exc),
                    duration_ms=_elapsed_ms(started),
                )

            log_task_finished(task_result)
            if diagnostics is not None:
                diagnostics.finish(task_result.model_dump(mode="json"))
            return task_result
        except asyncio.CancelledError:
            if diagnostics is not None:
                diagnostics.abort()
            raise
        finally:
            reset_current_diagnostics(token)

    @staticmethod
    def parse_request(request: TaskRequest | Mapping[str, Any]) -> TaskRequest:
        if isinstance(request, TaskRequest):
            return request
        if not isinstance(request, Mapping):
            raise _InvalidTaskError("Task request must be an object")
        try:
            probe = _TaskRequestProbe.model_validate(request)
        except ValidationError as exc:
            raise _InvalidTaskError(_validation_message(exc)) from exc
        if probe.version != SUPPORTED_TASK_VERSION:
            raise _InvalidTaskError(f"Unsupported task version: {probe.version}")
        if probe.type != SUPPORTED_TASK_TYPE:
            raise _UnsupportedTaskTypeError(probe.type)
        try:
            return TaskRequest.model_validate(request)
        except ValidationError as exc:
            raise _InvalidTaskError(_validation_message(exc)) from exc


class _InvalidTaskError(ValueError):
    pass


class _UnsupportedTaskTypeError(ValueError):
    def __init__(self, task_type: str) -> None:
        self.task_type = task_type
        super().__init__(f"Unsupported task type: {task_type}")


def _elapsed_ms(started: float) -> int:
    return max(int((time.perf_counter() - started) * 1000), 0)
