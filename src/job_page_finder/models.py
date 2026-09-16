from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from job_page_finder.contracts import (
    RETRYABLE_FINDER_FAILURE_CODES,
    FindJobPageFailure,
    FindJobPageRequest,
    FindJobPageResult,
    FindJobPageSuccess,
    JobEvidence,
)

FinderErrorCode = Literal[
    "BROWSER_INITIALIZATION_FAILED",
    "BROWSER_INITIALIZATION_TIMEOUT",
    "STEP_TIMEOUT",
    "MODEL_ERROR",
    "ACTION_ERROR",
    "VALIDATION_FAILED",
    "MAX_STEPS_REACHED",
]


class JobPageFinderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_url: HttpUrl
    max_steps: int = Field(default=8, ge=1, le=50)


class JobPageFinderResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool
    job_page_url: str | None = None
    job_title: str | None = None
    evidence: str | None = None
    steps: int
    error: str | None = None
    error_code: FinderErrorCode | None = None

    @model_validator(mode="after")
    def validate_success_fields(self) -> "JobPageFinderResult":
        if self.success and not (self.job_page_url and self.job_title and self.evidence):
            raise ValueError("successful result requires job_page_url, job_title, and evidence")
        return self


def to_legacy_finder_request(request: FindJobPageRequest) -> JobPageFinderInput:
    return JobPageFinderInput(company_url=request.company_url, max_steps=request.max_steps)


class LegacyFinderResultError(ValueError):
    def __init__(self, public_message: str) -> None:
        self.public_message = public_message
        super().__init__(public_message)


def from_legacy_finder_result(result: JobPageFinderResult) -> FindJobPageResult:
    if result.success:
        if not (result.job_page_url and result.job_title and result.evidence):
            raise LegacyFinderResultError("Finder returned a successful result without job page fields")
        # The stage 1 bridge must preserve strings accepted by the old public
        # executor boundary. Normal construction of the new contract remains validated.
        return FindJobPageSuccess.model_construct(
            status="succeeded",
            job_page_url=result.job_page_url,
            job_title=result.job_title,
            evidence=JobEvidence.model_construct(quote=result.evidence, source_url=result.job_page_url),
            steps=result.steps,
        )
    if result.error_code is None:
        raise LegacyFinderResultError(result.error or "Task failed")
    return FindJobPageFailure(
        status="failed",
        code=result.error_code,
        message=result.error or "Task failed",
        retryable=result.error_code in RETRYABLE_FINDER_FAILURE_CODES,
        steps=result.steps,
    )
