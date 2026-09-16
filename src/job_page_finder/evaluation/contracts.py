from __future__ import annotations

from collections.abc import Iterable, Sequence
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from job_page_finder.task_protocol import TaskResult

SCHEMA_VERSION = "v1"


class EvaluationError(ValueError):
    pass


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v1"] = SCHEMA_VERSION
    case_id: str = Field(min_length=1)
    company_name: str = Field(min_length=1)
    company_url: HttpUrl
    source: Literal["corpweb"]
    sample_bucket: str = Field(min_length=1)
    max_steps: int = Field(default=8, ge=1, le=50)

    @field_validator("case_id", "company_name", "source", "sample_bucket")
    @classmethod
    def strip_non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    def task_request(self) -> dict[str, Any]:
        return {
            "version": "v1",
            "task_id": self.case_id,
            "type": "find_job_page",
            "payload": {
                "company_url": str(self.company_url),
                "max_steps": self.max_steps,
            },
        }


class EvaluationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case: EvaluationCase
    result: TaskResult
    started_at: datetime
    finished_at: datetime


class EvaluationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int = Field(ge=0)
    completed: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
    success_rate: float | None


class EvaluationRunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v1"] = SCHEMA_VERSION
    run_id: str = Field(min_length=1)
    dataset_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    case_id: str = Field(min_length=1)
    attempt: int = Field(default=1, ge=1)
    execution_status: Literal["COMPLETED", "TIMEOUT", "RUNNER_ERROR"]
    started_at: datetime
    finished_at: datetime
    duration_ms: int = Field(ge=0)
    finder_result: TaskResult | None
    runner_error: str | None

    @model_validator(mode="after")
    def validate_payload(self) -> "EvaluationRunRecord":
        if self.execution_status == "COMPLETED":
            if self.finder_result is None or self.runner_error is not None:
                raise ValueError("completed records require finder_result and no runner_error")
        elif self.finder_result is not None or not self.runner_error:
            raise ValueError("non-completed records require runner_error and no finder_result")
        return self


class EvaluationCaseSource(Protocol):
    def load(self) -> Iterable[EvaluationCase]: ...


class EvaluationStoreSession(Protocol):
    def completed_case_ids(self) -> set[str]: ...

    def append(self, record: EvaluationRunRecord) -> None: ...

    def records(self) -> Sequence[EvaluationRunRecord]: ...

    def finish(self, summary: EvaluationSummary) -> None: ...


class EvaluationResultStore(Protocol):
    def open_campaign(self) -> AbstractAsyncContextManager[EvaluationStoreSession]: ...
