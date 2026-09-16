import uuid
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, TypeAdapter, ValidationError, model_validator

from job_page_finder.contracts import (
    FindJobPageFailure,
    FindJobPageRequest,
    FindJobPageResult,
    FindJobPageSuccess,
)

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


class FindJobPageTaskPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_url: HttpUrl
    max_steps: int = Field(default=8, ge=1, le=50)


class TaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal["v1"]
    task_id: str | None = None
    type: Literal["find_job_page"]
    payload: FindJobPageTaskPayload


class _TaskRequestProbe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    task_id: str | None = None
    type: str
    payload: Any


class FindJobPageTaskOutput(BaseModel):
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
    """Task v1 keeps duration under metadata per the approved DG-01 decision."""

    model_config = ConfigDict(extra="forbid")

    duration_ms: int = Field(ge=0)


class TaskResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal["v1"]
    task_id: str
    type: str
    status: Literal["succeeded", "failed"]
    output: FindJobPageTaskOutput | None = None
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


class TaskProtocolError(ValueError):
    def __init__(
        self,
        *,
        code: Literal["INVALID_TASK", "UNSUPPORTED_TASK_TYPE"],
        task_id: str,
        task_type: str,
        public_message: str,
    ) -> None:
        self.code = code
        self.task_id = task_id
        self.task_type = task_type
        self.public_message = public_message
        super().__init__(public_message)


def parse_task(raw: TaskRequest | Mapping[str, object]) -> TaskRequest:
    task_id, task_type = _extract_identity(raw)
    if isinstance(raw, TaskRequest):
        if raw.task_id == task_id:
            return raw
        return raw.model_copy(update={"task_id": task_id})
    if not isinstance(raw, Mapping):
        raise TaskProtocolError(
            code="INVALID_TASK",
            task_id=task_id,
            task_type=task_type,
            public_message="Task request must be an object",
        )
    try:
        probe = _TaskRequestProbe.model_validate(raw)
    except ValidationError as exc:
        raise TaskProtocolError(
            code="INVALID_TASK",
            task_id=task_id,
            task_type=task_type,
            public_message=_validation_message(exc),
        ) from exc
    if probe.version != SUPPORTED_TASK_VERSION:
        raise TaskProtocolError(
            code="INVALID_TASK",
            task_id=task_id,
            task_type=task_type,
            public_message=f"Unsupported task version: {probe.version}",
        )
    if probe.type != SUPPORTED_TASK_TYPE:
        raise TaskProtocolError(
            code="UNSUPPORTED_TASK_TYPE",
            task_id=task_id,
            task_type=probe.type,
            public_message=f"Unsupported task type: {probe.type}",
        )
    try:
        request = TaskRequest.model_validate(raw)
    except ValidationError as exc:
        raise TaskProtocolError(
            code="INVALID_TASK",
            task_id=task_id,
            task_type=task_type,
            public_message=_validation_message(exc),
        ) from exc
    if request.task_id != task_id:
        request = request.model_copy(update={"task_id": task_id})
    return request


def to_finder_request(payload: FindJobPageTaskPayload) -> FindJobPageRequest:
    return FindJobPageRequest(company_url=payload.company_url, max_steps=payload.max_steps)


def build_task_result(request: TaskRequest, result: FindJobPageResult, *, duration_ms: int) -> TaskResult:
    parsed_result = TypeAdapter(FindJobPageResult).validate_python(result)
    task_id = _resolve_task_id(request.task_id)
    if isinstance(parsed_result, FindJobPageFailure):
        return build_task_failure(
            task_id=task_id,
            task_type=request.type,
            code=parsed_result.code,
            message=parsed_result.message,
            duration_ms=duration_ms,
        )
    assert isinstance(parsed_result, FindJobPageSuccess)
    return TaskResult(
        version="v1",
        task_id=task_id,
        type=request.type,
        status="succeeded",
        output=FindJobPageTaskOutput(
            job_page_url=str(parsed_result.job_page_url),
            job_title=parsed_result.job_title,
            evidence=parsed_result.evidence.quote,
            steps=parsed_result.steps,
        ),
        error=None,
        metadata=TaskMetadata(duration_ms=max(duration_ms, 0)),
    )


def build_task_failure(
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


def _extract_identity(raw: object) -> tuple[str, str]:
    task_id: str | None = None
    task_type = "unknown"
    if isinstance(raw, TaskRequest):
        task_id = raw.task_id
        task_type = raw.type
    elif isinstance(raw, Mapping):
        raw_id = raw.get("task_id")
        if isinstance(raw_id, str):
            task_id = raw_id
        raw_type = raw.get("type")
        if isinstance(raw_type, str) and raw_type:
            task_type = raw_type
    return _resolve_task_id(task_id), task_type


def _resolve_task_id(value: str | None) -> str:
    if value is None or value == "":
        return uuid.uuid4().hex
    return value


def _validation_message(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "Invalid task request"
    error = errors[0]
    location = ".".join(str(part) for part in error.get("loc", ()))
    message = error.get("msg", "invalid")
    return f"Invalid task request: {location}: {message}" if location else f"Invalid task request: {message}"
