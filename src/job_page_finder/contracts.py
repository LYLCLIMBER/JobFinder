from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

FinderFailureCode = Literal[
    "BROWSER_INITIALIZATION_FAILED",
    "BROWSER_INITIALIZATION_TIMEOUT",
    "STEP_TIMEOUT",
    "MODEL_ERROR",
    "ACTION_ERROR",
    "VALIDATION_FAILED",
    "MAX_STEPS_REACHED",
]

RETRYABLE_FINDER_FAILURE_CODES: frozenset[str] = frozenset(
    {
        "BROWSER_INITIALIZATION_FAILED",
        "BROWSER_INITIALIZATION_TIMEOUT",
        "STEP_TIMEOUT",
        "MODEL_ERROR",
        "ACTION_ERROR",
    }
)


class FindJobPageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_url: HttpUrl
    max_steps: int = Field(default=8, ge=1, le=50)


class JobEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quote: str = Field(min_length=1)
    source_url: HttpUrl


class FindJobPageSuccess(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["succeeded"]
    job_page_url: HttpUrl
    job_title: str = Field(min_length=1)
    evidence: JobEvidence
    steps: int


class FindJobPageFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["failed"]
    code: FinderFailureCode
    message: str
    retryable: bool
    steps: int


FindJobPageResult = Annotated[FindJobPageSuccess | FindJobPageFailure, Field(discriminator="status")]
