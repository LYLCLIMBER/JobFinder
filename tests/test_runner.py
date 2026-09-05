import json
import logging

import pytest
from pydantic import ValidationError

from job_page_finder.models import JobPageFinderInput, JobPageFinderResult
from job_page_finder.runner import TaskRequest, TaskRunner


def success_result() -> JobPageFinderResult:
    return JobPageFinderResult(
        success=True,
        job_page_url="https://example.com/careers",
        job_title="Senior Backend Engineer",
        evidence="Senior Backend Engineer",
        steps=3,
    )


def valid_request(**overrides) -> dict:
    request = {
        "version": "v1",
        "type": "find_job_page",
        "payload": {"company_url": "https://example.com", "max_steps": 8},
    }
    request.update(overrides)
    return request


@pytest.mark.asyncio
async def test_run_returns_unified_success_result() -> None:
    """Return the original job fields inside a unified succeeded TaskResult."""
    calls: list[JobPageFinderInput] = []

    async def fake_find(finder_input: JobPageFinderInput) -> JobPageFinderResult:
        calls.append(finder_input)
        return success_result()

    result = await TaskRunner(fake_find).run(valid_request())

    assert result.status == "succeeded"
    assert result.version == "v1"
    assert result.type == "find_job_page"
    assert result.output is not None
    assert result.output.job_page_url == "https://example.com/careers"
    assert result.output.job_title == "Senior Backend Engineer"
    assert result.output.evidence == "Senior Backend Engineer"
    assert result.output.steps == 3
    assert result.error is None
    assert result.metadata.duration_ms >= 0
    assert len(calls) == 1
    assert str(calls[0].company_url) == "https://example.com/"


@pytest.mark.asyncio
async def test_generates_task_id_when_missing_and_preserves_provided_id(caplog) -> None:
    """Generate a non-empty task ID or keep the caller-provided value in the result and logs."""
    caplog.set_level(logging.INFO, logger="job_page_finder.runner")

    async def fake_find(finder_input: JobPageFinderInput) -> JobPageFinderResult:
        return success_result()

    runner = TaskRunner(fake_find)
    generated = await runner.run(valid_request())
    started = [record.message for record in caplog.records if record.message.startswith("task started")]
    finished = [record.message for record in caplog.records if record.message.startswith("task finished")]
    assert generated.task_id
    assert len(started) == 1
    assert len(finished) == 1
    assert f"task_id={generated.task_id}" in started[0]
    assert f"task_id={generated.task_id}" in finished[0]

    caplog.clear()
    preserved = await runner.run(valid_request(task_id="caller-task-123"))
    started = [record.message for record in caplog.records if record.message.startswith("task started")]
    finished = [record.message for record in caplog.records if record.message.startswith("task finished")]
    assert preserved.task_id == "caller-task-123"
    assert started[0].startswith("task started task_id=caller-task-123 ")
    assert finished[0].startswith("task finished task_id=caller-task-123 ")
    assert "task_type=find_job_page" in caplog.text


@pytest.mark.asyncio
async def test_rejects_invalid_requests_before_calling_finder() -> None:
    """Reject unknown versions, extra fields, and illegal payloads before execution."""
    calls: list[JobPageFinderInput] = []

    async def fake_find(finder_input: JobPageFinderInput) -> JobPageFinderResult:
        calls.append(finder_input)
        raise AssertionError("finder should not run")

    runner = TaskRunner(fake_find)
    unknown_version = await runner.run(valid_request(version="v2"))
    extra_field = await runner.run({**valid_request(), "unexpected": True})
    illegal_payload = await runner.run(valid_request(payload={"company_url": "not-a-url"}))

    for result in (unknown_version, extra_field, illegal_payload):
        assert result.status == "failed"
        assert result.error is not None
        assert result.error.code == "INVALID_TASK"
        assert result.output is None
        assert result.error.retryable is False
    assert calls == []


@pytest.mark.asyncio
async def test_rejects_unknown_task_type_before_calling_finder() -> None:
    """Return UNSUPPORTED_TASK_TYPE without executing the finder."""
    calls: list[JobPageFinderInput] = []

    async def fake_find(finder_input: JobPageFinderInput) -> JobPageFinderResult:
        calls.append(finder_input)
        raise AssertionError("finder should not run")

    result = await TaskRunner(fake_find).run(valid_request(type="extract_jobs"))

    assert result.status == "failed"
    assert result.error is not None
    assert result.error.code == "UNSUPPORTED_TASK_TYPE"
    assert result.type == "extract_jobs"
    assert calls == []


@pytest.mark.asyncio
async def test_maps_finder_error_code_without_parsing_message() -> None:
    """Map Finder failures from error_code even when the message names a different failure."""

    async def fake_find(finder_input: JobPageFinderInput) -> JobPageFinderResult:
        return JobPageFinderResult(
            success=False,
            steps=1,
            error="Maximum steps reached without finding a specific job",
            error_code="STEP_TIMEOUT",
        )

    result = await TaskRunner(fake_find).run(valid_request())

    assert result.status == "failed"
    assert result.error is not None
    assert result.error.code == "STEP_TIMEOUT"
    assert result.error.message == "Maximum steps reached without finding a specific job"
    assert result.error.retryable is True
    assert result.output is None


@pytest.mark.asyncio
async def test_unhandled_exception_becomes_internal_error_without_traceback() -> None:
    """Normalize unexpected exceptions to INTERNAL_ERROR without exposing a traceback."""

    async def fake_find(finder_input: JobPageFinderInput) -> JobPageFinderResult:
        raise RuntimeError("boom")

    result = await TaskRunner(fake_find).run(valid_request())
    payload = json.loads(result.model_dump_json())

    assert result.status == "failed"
    assert result.error is not None
    assert result.error.code == "INTERNAL_ERROR"
    assert result.error.message == "RuntimeError: boom"
    assert "Traceback" not in result.error.message
    assert "Traceback" not in json.dumps(payload)


@pytest.mark.asyncio
async def test_logs_task_metadata_without_sensitive_fields(caplog) -> None:
    """Include task identity and status in logs without secrets or large page payloads."""
    caplog.set_level(logging.INFO, logger="job_page_finder.runner")

    async def fake_find(finder_input: JobPageFinderInput) -> JobPageFinderResult:
        return success_result()

    result = await TaskRunner(fake_find).run(valid_request(task_id="log-task"))
    text = caplog.text

    assert result.status == "succeeded"
    assert "task_id=log-task" in text
    assert "task_type=find_job_page" in text
    assert "status=succeeded" in text
    assert "duration_ms=" in text
    assert "sk-" not in text
    assert "<html" not in text
    assert "base64" not in text.lower()
    assert "data:image" not in text


@pytest.mark.asyncio
async def test_incomplete_finder_success_is_internal_error() -> None:
    """Do not treat a successful Finder result without job fields as a succeeded task."""

    async def fake_find(finder_input: JobPageFinderInput) -> JobPageFinderResult:
        return JobPageFinderResult.model_construct(
            success=True,
            steps=1,
            job_page_url=None,
            job_title=None,
            evidence=None,
        )

    result = await TaskRunner(fake_find).run(valid_request())

    assert result.status == "failed"
    assert result.output is None
    assert result.error is not None
    assert result.error.code == "INTERNAL_ERROR"


def test_successful_finder_result_requires_job_fields() -> None:
    """Reject constructing a successful domain result without job page fields."""
    with pytest.raises(ValidationError, match="successful result requires"):
        JobPageFinderResult(success=True, steps=1)


@pytest.mark.asyncio
async def test_accepts_typed_task_request() -> None:
    """Execute a already-validated TaskRequest through the same runner path."""

    async def fake_find(finder_input: JobPageFinderInput) -> JobPageFinderResult:
        return success_result()

    request = TaskRequest.model_validate(valid_request(task_id="typed-id"))
    result = await TaskRunner(fake_find).run(request)

    assert result.status == "succeeded"
    assert result.task_id == "typed-id"
