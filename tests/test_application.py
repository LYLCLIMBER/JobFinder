import asyncio
import json
import logging

import pytest

from job_page_finder.application import TaskApplication
from job_page_finder.contracts import FindJobPageRequest, FindJobPageSuccess, JobEvidence
from job_page_finder.models import JobPageFinderInput, JobPageFinderResult
from job_page_finder.null_diagnostics import NullDiagnosticsFactory
from job_page_finder.runner import TaskRunner
from job_page_finder.task_protocol import TaskRequest


def success_result() -> FindJobPageSuccess:
    return FindJobPageSuccess(
        status="succeeded",
        job_page_url="https://example.com/careers",
        job_title="Senior Backend Engineer",
        evidence=JobEvidence(quote="Senior Backend Engineer", source_url="https://example.com/careers"),
        steps=3,
    )


def valid_request(**overrides: object) -> dict:
    request = {
        "version": "v1",
        "type": "find_job_page",
        "payload": {"company_url": "https://example.com", "max_steps": 8},
    }
    request.update(overrides)
    return request


class RecordingProvider:
    def __init__(self, finder: object | None = None, error: Exception | None = None) -> None:
        self.calls = 0
        self._finder = finder
        self._error = error

    def get(self) -> object:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._finder


class FakeFinder:
    def __init__(self, result: FindJobPageSuccess | BaseException) -> None:
        self.result = result
        self.requests: list[object] = []
        self.events: list[object] = []

    async def find(self, request: FindJobPageRequest, *, events: object = None) -> FindJobPageSuccess:
        self.requests.append(request)
        self.events.append(events)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class ThrowingDiagnostics:
    @property
    def finder_events(self) -> object:
        raise RuntimeError("events failed")

    def task_started(self) -> None:
        raise RuntimeError("start failed")

    def task_finished(self, result: object) -> None:
        raise RuntimeError("finish failed")

    def abort(self) -> None:
        raise RuntimeError("abort failed")

    def close(self) -> None:
        raise RuntimeError("close failed")


class ThrowingDiagnosticsFactory:
    def create(self, *, task_id: str, task_type: str) -> ThrowingDiagnostics:
        return ThrowingDiagnostics()


class FailingDiagnosticsFactory:
    def create(self, *, task_id: str, task_type: str) -> object:
        raise RuntimeError("factory failed")


class SelfCancellingDiagnostics(ThrowingDiagnostics):
    def task_started(self) -> None:
        raise asyncio.CancelledError

    def task_finished(self, result: object) -> None:
        raise asyncio.CancelledError

    def close(self) -> None:
        raise asyncio.CancelledError


class SelfCancellingDiagnosticsFactory:
    def create(self, *, task_id: str, task_type: str) -> SelfCancellingDiagnostics:
        return SelfCancellingDiagnostics()


class SelfCancellingFactory:
    def create(self, *, task_id: str, task_type: str) -> object:
        raise asyncio.CancelledError


def application_for(finder: FakeFinder | None = None, *, provider: RecordingProvider | None = None) -> TaskApplication:
    if provider is None:
        provider = RecordingProvider(finder or FakeFinder(success_result()))
    return TaskApplication(provider, NullDiagnosticsFactory())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_run_returns_unified_success_result() -> None:
    finder = FakeFinder(success_result())
    result = await application_for(finder).run(valid_request())

    assert result.status == "succeeded"
    assert result.output is not None
    assert result.output.job_page_url == "https://example.com/careers"
    assert result.output.job_title == "Senior Backend Engineer"
    assert result.output.evidence == "Senior Backend Engineer"
    assert result.output.steps == 3
    assert result.error is None
    assert result.metadata.duration_ms >= 0
    assert len(finder.requests) == 1
    assert str(finder.requests[0].company_url) == "https://example.com/"
    assert finder.events[0] is not None


@pytest.mark.asyncio
async def test_invalid_request_does_not_call_provider() -> None:
    provider = RecordingProvider(FakeFinder(success_result()))
    app = application_for(provider=provider)

    unknown_version = await app.run(valid_request(version="v2"))
    extra_field = await app.run({**valid_request(), "unexpected": True})
    illegal_payload = await app.run(valid_request(payload={"company_url": "not-a-url"}))
    unknown_type = await app.run(valid_request(type="extract_jobs"))

    assert provider.calls == 0
    assert unknown_version.error is not None and unknown_version.error.code == "INVALID_TASK"
    assert extra_field.error is not None and extra_field.error.code == "INVALID_TASK"
    assert illegal_payload.error is not None and illegal_payload.error.code == "INVALID_TASK"
    assert unknown_type.error is not None and unknown_type.error.code == "UNSUPPORTED_TASK_TYPE"
    assert unknown_type.type == "extract_jobs"
    assert unknown_type.error.retryable is False


@pytest.mark.asyncio
async def test_provider_failure_is_configuration_error() -> None:
    provider = RecordingProvider(error=RuntimeError("DEEPSEEK_API_KEY is not configured"))
    result = await application_for(provider=provider).run(valid_request(task_id="config-task"))

    assert provider.calls == 1
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.code == "CONFIGURATION_ERROR"
    assert result.error.retryable is False
    assert result.task_id == "config-task"


@pytest.mark.asyncio
async def test_unhandled_exception_becomes_internal_error_without_traceback() -> None:
    finder = FakeFinder(RuntimeError("boom"))
    result = await application_for(finder).run(valid_request())
    payload = json.loads(result.model_dump_json())

    assert result.error is not None
    assert result.error.code == "INTERNAL_ERROR"
    assert result.error.message == "RuntimeError: boom"
    assert "Traceback" not in result.error.message
    assert "Traceback" not in json.dumps(payload)


@pytest.mark.asyncio
async def test_cancellation_is_not_internal_error() -> None:
    finder = FakeFinder(asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await application_for(finder).run(valid_request())


@pytest.mark.asyncio
async def test_throwing_diagnostics_do_not_change_result_or_cancellation() -> None:
    application = TaskApplication(RecordingProvider(FakeFinder(success_result())), ThrowingDiagnosticsFactory())  # type: ignore[arg-type]
    result = await application.run(valid_request())
    assert result.status == "succeeded"

    cancelled = TaskApplication(RecordingProvider(FakeFinder(asyncio.CancelledError())), ThrowingDiagnosticsFactory())  # type: ignore[arg-type]
    with pytest.raises(asyncio.CancelledError):
        await cancelled.run(valid_request())

    factory_failure = TaskApplication(RecordingProvider(FakeFinder(success_result())), FailingDiagnosticsFactory())  # type: ignore[arg-type]
    assert (await factory_failure.run(valid_request())).status == "succeeded"

    self_cancelled = TaskApplication(
        RecordingProvider(FakeFinder(success_result())), SelfCancellingDiagnosticsFactory()
    )  # type: ignore[arg-type]
    assert (await self_cancelled.run(valid_request())).status == "succeeded"

    factory_self_cancelled = TaskApplication(RecordingProvider(FakeFinder(success_result())), SelfCancellingFactory())  # type: ignore[arg-type]
    assert (await factory_self_cancelled.run(valid_request())).status == "succeeded"


@pytest.mark.asyncio
async def test_logs_task_identity_once(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="job_page_finder.application")
    result = await application_for().run(valid_request(task_id="log-task"))
    started = [record.message for record in caplog.records if record.message.startswith("task started")]
    finished = [record.message for record in caplog.records if record.message.startswith("task finished")]

    assert result.task_id == "log-task"
    assert started == ["task started task_id=log-task task_type=find_job_page"]
    assert len(finished) == 1
    assert finished[0].startswith("task finished task_id=log-task task_type=find_job_page status=succeeded")


@pytest.mark.asyncio
async def test_task_runner_facade_matches_application_result() -> None:
    async def fake_find(finder_input: JobPageFinderInput) -> JobPageFinderResult:
        return JobPageFinderResult(
            success=True,
            job_page_url="https://example.com/careers",
            job_title="Senior Backend Engineer",
            evidence="Senior Backend Engineer",
            steps=3,
        )

    request = valid_request(task_id="same-task")
    finder = FakeFinder(success_result())
    application_result = await application_for(finder).run(request)
    facade_result = await TaskRunner(fake_find).run(request)

    assert facade_result.model_dump(exclude={"metadata"}) == application_result.model_dump(exclude={"metadata"})
    assert facade_result.status == "succeeded"
    assert facade_result.task_id == "same-task"


@pytest.mark.asyncio
async def test_accepts_typed_task_request() -> None:
    request = TaskRequest.model_validate(valid_request(task_id="typed-id"))
    result = await application_for().run(request)
    assert result.status == "succeeded"
    assert result.task_id == "typed-id"
