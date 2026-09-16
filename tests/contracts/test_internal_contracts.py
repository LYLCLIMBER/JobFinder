import pytest
from pydantic import TypeAdapter, ValidationError

from job_page_finder.contracts import (
    FindJobPageFailure,
    FindJobPageRequest,
    FindJobPageResult,
    FindJobPageSuccess,
)
from job_page_finder.models import (
    JobPageFinderInput,
    JobPageFinderResult,
    from_legacy_finder_result,
    to_legacy_finder_request,
)
from job_page_finder.task_protocol import FindJobPageTaskPayload, to_finder_request


def test_task_payload_maps_explicitly_to_internal_and_legacy_requests() -> None:
    payload = FindJobPageTaskPayload(company_url="https://example.com", max_steps=5)

    request = to_finder_request(payload)
    legacy_request = to_legacy_finder_request(request)

    assert isinstance(request, FindJobPageRequest)
    assert isinstance(legacy_request, JobPageFinderInput)
    assert request.model_dump(mode="json") == legacy_request.model_dump(mode="json")


def test_legacy_success_maps_to_complete_discriminated_result() -> None:
    result = from_legacy_finder_result(
        JobPageFinderResult(
            success=True,
            job_page_url="https://example.com/jobs",
            job_title="Engineer",
            evidence="Engineer",
            steps=2,
        )
    )

    assert isinstance(result, FindJobPageSuccess)
    assert result.status == "succeeded"
    assert result.evidence.quote == "Engineer"
    assert str(result.evidence.source_url) == "https://example.com/jobs"


def test_legacy_mapper_preserves_root_url_and_unconstrained_steps() -> None:
    result = from_legacy_finder_result(
        JobPageFinderResult(
            success=True,
            job_page_url="https://example.com",
            job_title="Engineer",
            evidence="Engineer",
            steps=-1,
        )
    )

    assert isinstance(result, FindJobPageSuccess)
    assert result.job_page_url == "https://example.com"
    assert result.steps == -1


def test_legacy_failure_maps_code_retryability_and_steps() -> None:
    result = from_legacy_finder_result(
        JobPageFinderResult(success=False, error="timed out", error_code="STEP_TIMEOUT", steps=1)
    )

    assert isinstance(result, FindJobPageFailure)
    assert result.status == "failed"
    assert result.code == "STEP_TIMEOUT"
    assert result.retryable is True
    assert result.steps == 1


def test_internal_result_union_rejects_incomplete_success() -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(FindJobPageResult).validate_python(
            {
                "status": "succeeded",
                "job_page_url": "https://example.com/jobs",
                "job_title": "Engineer",
                "steps": 1,
            }
        )
