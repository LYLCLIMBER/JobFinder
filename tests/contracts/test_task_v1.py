from typing import get_args

import pytest
from pydantic import ValidationError

from job_page_finder.contracts import FindJobPageRequest
from job_page_finder.models import JobPageFinderInput, JobPageFinderResult
from job_page_finder.runner import TaskRunner
from job_page_finder.task_protocol import (
    FindJobPageTaskPayload,
    TaskErrorCode,
    TaskMetadata,
    TaskProtocolError,
    TaskRequest,
    TaskResult,
    build_task_failure,
    parse_task,
)

ERROR_RETRYABILITY: dict[TaskErrorCode, bool] = {
    "INVALID_TASK": False,
    "UNSUPPORTED_TASK_TYPE": False,
    "CONFIGURATION_ERROR": False,
    "BROWSER_INITIALIZATION_FAILED": True,
    "BROWSER_INITIALIZATION_TIMEOUT": True,
    "STEP_TIMEOUT": True,
    "MODEL_ERROR": True,
    "ACTION_ERROR": True,
    "VALIDATION_FAILED": False,
    "MAX_STEPS_REACHED": False,
    "INTERNAL_ERROR": True,
}


def valid_request() -> dict:
    return {
        "version": "v1",
        "task_id": "contract-task",
        "type": "find_job_page",
        "payload": {"company_url": "https://example.com", "max_steps": 8},
    }


def test_task_request_schema_preserves_current_v1_payload() -> None:
    request = TaskRequest.model_validate(valid_request())

    assert set(get_args(TaskErrorCode)) == set(ERROR_RETRYABILITY)
    assert request.model_dump(mode="json") == {
        **valid_request(),
        "payload": {"company_url": "https://example.com/", "max_steps": 8},
    }
    assert isinstance(request.payload, FindJobPageTaskPayload)
    assert not isinstance(request.payload, (JobPageFinderInput, FindJobPageRequest))
    assert request.payload.max_steps == 8


@pytest.mark.parametrize(
    "change",
    [
        {"unexpected": True},
        {"payload": {"company_url": "https://example.com", "max_steps": 8, "unexpected": True}},
        {"payload": {"company_url": "ftp://example.com", "max_steps": 8}},
        {"payload": {"company_url": "https://example.com", "max_steps": 0}},
        {"payload": {"company_url": "https://example.com", "max_steps": 51}},
    ],
)
def test_task_request_rejects_unknown_or_invalid_fields(change: dict) -> None:
    raw = valid_request()
    raw.update(change)

    with pytest.raises(ValidationError):
        TaskRequest.model_validate(raw)


def test_parse_task_generates_identity_and_exposes_structured_protocol_errors() -> None:
    raw = valid_request()
    raw.pop("task_id")

    parsed = parse_task(raw)

    assert parsed.task_id
    with pytest.raises(TaskProtocolError) as caught:
        parse_task({**raw, "task_id": "unsupported-task", "type": "extract_jobs"})
    assert caught.value.code == "UNSUPPORTED_TASK_TYPE"
    assert caught.value.task_id == "unsupported-task"
    assert caught.value.task_type == "extract_jobs"


@pytest.mark.asyncio
async def test_task_success_json_contract_keeps_duration_in_metadata() -> None:
    async def fake_find(_: JobPageFinderInput) -> JobPageFinderResult:
        return JobPageFinderResult(
            success=True,
            job_page_url="https://example.com/careers",
            job_title="Senior Backend Engineer",
            evidence="Senior Backend Engineer",
            steps=3,
        )

    result = await TaskRunner(fake_find).run(valid_request())
    payload = result.model_dump(mode="json")

    assert payload == {
        "version": "v1",
        "task_id": "contract-task",
        "type": "find_job_page",
        "status": "succeeded",
        "output": {
            "job_page_url": "https://example.com/careers",
            "job_title": "Senior Backend Engineer",
            "evidence": "Senior Backend Engineer",
            "steps": 3,
        },
        "error": None,
        "metadata": {"duration_ms": payload["metadata"]["duration_ms"]},
    }
    assert isinstance(payload["metadata"]["duration_ms"], int)
    assert payload["metadata"]["duration_ms"] >= 0
    assert "duration_ms" not in payload


@pytest.mark.parametrize(("code", "retryable"), ERROR_RETRYABILITY.items())
def test_task_failure_json_contract_and_retryability(code: TaskErrorCode, retryable: bool) -> None:
    result = build_task_failure(
        task_id="failed-task",
        task_type="find_job_page",
        code=code,
        message="public failure",
        duration_ms=-1,
    )

    assert result.model_dump(mode="json") == {
        "version": "v1",
        "task_id": "failed-task",
        "type": "find_job_page",
        "status": "failed",
        "output": None,
        "error": {
            "code": code,
            "message": "public failure",
            "retryable": retryable,
        },
        "metadata": {"duration_ms": 0},
    }


@pytest.mark.parametrize(
    ("status", "output", "error"),
    [
        ("succeeded", None, None),
        (
            "succeeded",
            {"job_page_url": "https://example.com/jobs", "job_title": "Engineer", "evidence": "Engineer", "steps": 1},
            {"code": "INTERNAL_ERROR", "message": "failure", "retryable": True},
        ),
        ("failed", None, None),
        (
            "failed",
            {"job_page_url": "https://example.com/jobs", "job_title": "Engineer", "evidence": "Engineer", "steps": 1},
            {"code": "INTERNAL_ERROR", "message": "failure", "retryable": True},
        ),
    ],
)
def test_task_result_rejects_inconsistent_status_payloads(status: str, output: dict | None, error: dict | None) -> None:
    with pytest.raises(ValidationError):
        TaskResult(
            version="v1",
            task_id="inconsistent-result",
            type="find_job_page",
            status=status,
            output=output,
            error=error,
            metadata=TaskMetadata(duration_ms=0),
        )
