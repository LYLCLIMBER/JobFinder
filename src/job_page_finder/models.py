from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

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


class ClickAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["click"]
    index: int = Field(ge=1)


class ScrollAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["scroll"]
    direction: Literal["up", "down"]


class WaitAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["wait"]
    seconds: int = Field(ge=1, le=5)


class DoneAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["done"]
    job_title: str = Field(min_length=1)
    evidence: str = Field(min_length=1)


FinderAction = Annotated[ClickAction | ScrollAction | WaitAction | DoneAction, Field(discriminator="type")]


class AgentDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: FinderAction
