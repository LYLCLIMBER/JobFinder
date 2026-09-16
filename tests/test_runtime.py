import asyncio
import inspect
import logging

import pytest

import job_page_finder
from job_page_finder import api, cli, runtime
from job_page_finder.models import JobPageFinderInput, JobPageFinderResult
from job_page_finder.runner import TaskRunner
from job_page_finder.runtime import RuntimeConfig, create_runner, run_task, settings_from_config
from job_page_finder.settings import DiagnosticsSettings, FinderSettings


def test_package_root_exports_only_the_stable_api() -> None:
    assert job_page_finder.__all__ == [
        "DiagnosticsSettings",
        "RuntimeSettings",
        "TaskRequest",
        "TaskResult",
        "run_task",
    ]


def test_build_application_is_the_only_composition_root() -> None:
    """Keep Finder assembly in runtime.build_application so API and CLI reuse it."""
    cli_source = inspect.getsource(cli)
    api_source = inspect.getsource(api)
    runtime_source = inspect.getsource(runtime)

    assert "build_application" in cli_source
    assert "JobPageFinder(" not in cli_source
    assert "create_deepseek_llm" not in cli_source
    assert "parse_task" not in api_source
    assert "build_application" in api_source
    assert "JobPageFinder(" in runtime_source
    assert "create_deepseek_llm" in runtime_source
    assert "def build_application" in runtime_source


def test_create_runner_wires_injected_dependencies(monkeypatch) -> None:
    """Assemble Finder from injected LLM, tools, browser factory, and runtime config."""
    captured: dict = {}

    class FakeFinder:
        def __init__(self, browser_factory, action_model, **kwargs) -> None:
            captured["browser_factory"] = browser_factory
            captured["action_model"] = action_model
            captured.update(kwargs)

    def should_not_load_environment(**kwargs):
        raise AssertionError("should not load environment")

    monkeypatch.setattr("job_page_finder.runtime.JobPageFinder", FakeFinder)
    monkeypatch.setattr("job_page_finder.runtime.create_deepseek_llm", should_not_load_environment)
    llm = object()
    tools = object()
    factory = object()

    runner = create_runner(
        llm=llm,
        tools=tools,
        browser_factory=factory,
        config=RuntimeConfig(step_timeout=9, use_vision=True, max_visual_candidates=4),
    )

    assert isinstance(runner, TaskRunner)
    assert captured == {}
    asyncio.run(
        runner.run(
            {
                "version": "v1",
                "type": "find_job_page",
                "payload": {"company_url": "https://example.com"},
            }
        )
    )
    assert captured["action_model"]._llm is llm
    assert captured["browser_factory"]._browser_factory is factory
    assert captured["browser_factory"]._tools is tools
    assert captured["settings"].step_timeout == 9
    assert captured["settings"].use_vision is True
    assert captured["browser_factory"].max_visual_candidates == 4


def test_default_vision_is_enabled_and_can_be_explicitly_disabled(monkeypatch) -> None:
    captured: list[bool] = []

    class FakeFinder:
        def __init__(self, browser_factory, action_model, **kwargs) -> None:
            captured.append(kwargs["settings"].use_vision)

    monkeypatch.setattr("job_page_finder.runtime.JobPageFinder", FakeFinder)

    assert RuntimeConfig().use_vision is True
    assert FinderSettings().use_vision is True
    request = {
        "version": "v1",
        "type": "find_job_page",
        "payload": {"company_url": "https://example.com"},
    }
    asyncio.run(create_runner(llm=object()).run(request))
    asyncio.run(create_runner(llm=object(), config=RuntimeConfig(use_vision=False)).run(request))

    assert captured == [True, False]


def test_diagnostics_screenshot_override_survives_runtime_settings_mapping() -> None:
    settings = settings_from_config(RuntimeConfig(diagnostics_level="raw", diagnostics_screenshots=False))

    assert settings.diagnostics.level == "raw"
    assert settings.diagnostics.capture_screenshots is False
    assert DiagnosticsSettings().capture_screenshots is None


@pytest.mark.asyncio
async def test_run_task_uses_create_runner_unless_runner_is_injected(monkeypatch) -> None:
    """Create a runner through the composition root when the caller does not supply one."""
    calls: list[dict] = []

    async def fake_find(finder_input: JobPageFinderInput) -> JobPageFinderResult:
        return JobPageFinderResult(
            success=True,
            job_page_url="https://example.com/careers",
            job_title="Senior Backend Engineer",
            evidence="Senior Backend Engineer",
            steps=1,
        )

    class FakeApplication:
        async def run(self, request, *, emit_started: bool = True):
            from job_page_finder.models import from_legacy_finder_result, to_legacy_finder_request
            from job_page_finder.task_protocol import build_task_result, parse_task, to_finder_request

            parsed = parse_task(request)
            legacy = await fake_find(to_legacy_finder_request(to_finder_request(parsed.payload)))
            return build_task_result(parsed, from_legacy_finder_result(legacy), duration_ms=1)

    def fake_build_application(*args, **kwargs):
        calls.append(kwargs)
        return FakeApplication()

    monkeypatch.setattr("job_page_finder.runtime.build_application", fake_build_application)
    request = {
        "version": "v1",
        "type": "find_job_page",
        "payload": {"company_url": "https://example.com"},
    }

    result = await run_task(request, llm=object())
    assert result.status == "succeeded"
    assert len(calls) == 1
    assert calls[0]["llm"] is not None

    second = await run_task(request, runner=TaskRunner(fake_find))
    assert second.status == "succeeded"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_run_task_default_diagnostics_root_uses_test_tmp_path(isolate_default_diagnostics_root) -> None:
    async def fake_find(finder_input: JobPageFinderInput) -> JobPageFinderResult:
        return JobPageFinderResult(
            success=True,
            job_page_url="https://example.com/careers",
            job_title="Senior Backend Engineer",
            evidence="Senior Backend Engineer",
            steps=1,
        )

    result = await run_task(
        {
            "version": "v1",
            "task_id": "isolated-runtime",
            "type": "find_job_page",
            "payload": {"company_url": "https://example.com"},
        },
        runner=TaskRunner(fake_find),
    )

    assert result.status == "succeeded"
    assert RuntimeConfig().diagnostics_root == isolate_default_diagnostics_root
    assert len([path for path in isolate_default_diagnostics_root.iterdir() if path.is_dir()]) == 1


@pytest.mark.asyncio
async def test_run_task_maps_assembly_failures_to_configuration_error(monkeypatch) -> None:
    """Return CONFIGURATION_ERROR when the composition root cannot be created."""

    def boom(**kwargs):
        raise RuntimeError("DEEPSEEK_API_KEY is not configured")

    monkeypatch.setattr("job_page_finder.runtime.create_deepseek_llm", boom)
    result = await run_task(
        {
            "version": "v1",
            "task_id": "cfg-1",
            "type": "find_job_page",
            "payload": {"company_url": "https://example.com"},
        }
    )

    assert result.status == "failed"
    assert result.task_id == "cfg-1"
    assert result.error is not None
    assert result.error.code == "CONFIGURATION_ERROR"
    assert result.error.retryable is False


@pytest.mark.asyncio
async def test_run_task_rejects_invalid_requests_before_create_runner(monkeypatch) -> None:
    """Validate the task envelope before assembling LLM, Finder, or browser dependencies."""

    def boom(**kwargs):
        raise RuntimeError("DEEPSEEK_API_KEY is not configured")

    monkeypatch.setattr("job_page_finder.runtime.create_deepseek_llm", boom)
    invalid_version = await run_task(
        {
            "version": "v2",
            "task_id": "invalid-1",
            "type": "find_job_page",
            "payload": {"company_url": "https://example.com"},
        }
    )
    unknown_type = await run_task(
        {
            "version": "v1",
            "task_id": "invalid-2",
            "type": "extract_jobs",
            "payload": {"company_url": "https://example.com"},
        }
    )
    illegal_payload = await run_task(
        {
            "version": "v1",
            "task_id": "invalid-3",
            "type": "find_job_page",
            "payload": {"company_url": "not-a-url"},
        }
    )

    assert invalid_version.error is not None
    assert invalid_version.error.code == "INVALID_TASK"
    assert invalid_version.task_id == "invalid-1"
    assert unknown_type.error is not None
    assert unknown_type.error.code == "UNSUPPORTED_TASK_TYPE"
    assert unknown_type.task_id == "invalid-2"
    assert illegal_payload.error is not None
    assert illegal_payload.error.code == "INVALID_TASK"
    assert illegal_payload.task_id == "invalid-3"


def _task_log_events(caplog) -> list[tuple[str, str]]:
    events: list[tuple[str, str]] = []
    for record in caplog.records:
        if record.message.startswith("task started"):
            events.append(("started", record.message))
        elif record.message.startswith("task finished"):
            events.append(("finished", record.message))
    return events


def _assert_single_started_then_finished(
    caplog,
    *,
    task_id: str,
    task_type: str,
    error_code: str,
) -> None:
    events = _task_log_events(caplog)
    assert [kind for kind, _ in events] == ["started", "finished"]
    assert events[0][1] == f"task started task_id={task_id} task_type={task_type}"
    assert events[1][1].startswith(f"task finished task_id={task_id} task_type={task_type} status=failed")
    assert f"error_code={error_code}" in events[1][1]


@pytest.mark.asyncio
async def test_run_task_logs_invalid_requests(monkeypatch, caplog) -> None:
    """Log started before validation and finished after INVALID_TASK."""
    caplog.set_level(logging.INFO, logger="job_page_finder")

    def boom(**kwargs):
        raise AssertionError("provider assembly should not run for invalid requests")

    monkeypatch.setattr("job_page_finder.runtime.create_deepseek_llm", boom)
    result = await run_task(
        {
            "version": "v2",
            "task_id": "log-invalid",
            "type": "find_job_page",
            "payload": {"company_url": "https://example.com"},
        }
    )

    assert result.error is not None
    assert result.error.code == "INVALID_TASK"
    assert result.task_id == "log-invalid"
    _assert_single_started_then_finished(
        caplog,
        task_id="log-invalid",
        task_type="find_job_page",
        error_code="INVALID_TASK",
    )


@pytest.mark.asyncio
async def test_run_task_logs_unsupported_task_type(monkeypatch, caplog) -> None:
    """Log started before validation and finished after UNSUPPORTED_TASK_TYPE."""
    caplog.set_level(logging.INFO, logger="job_page_finder")

    def boom(**kwargs):
        raise AssertionError("provider assembly should not run for unsupported types")

    monkeypatch.setattr("job_page_finder.runtime.create_deepseek_llm", boom)
    result = await run_task(
        {
            "version": "v1",
            "task_id": "log-unsupported",
            "type": "extract_jobs",
            "payload": {"company_url": "https://example.com"},
        }
    )

    assert result.error is not None
    assert result.error.code == "UNSUPPORTED_TASK_TYPE"
    assert result.task_id == "log-unsupported"
    _assert_single_started_then_finished(
        caplog,
        task_id="log-unsupported",
        task_type="extract_jobs",
        error_code="UNSUPPORTED_TASK_TYPE",
    )


@pytest.mark.asyncio
async def test_run_task_logs_configuration_errors(monkeypatch, caplog) -> None:
    """Log started before composition and finished after CONFIGURATION_ERROR."""
    caplog.set_level(logging.INFO, logger="job_page_finder")
    saw_started_before_assembly = False

    def boom(**kwargs):
        nonlocal saw_started_before_assembly
        saw_started_before_assembly = any(
            record.message.startswith("task started task_id=log-config") for record in caplog.records
        )
        raise RuntimeError("DEEPSEEK_API_KEY is not configured")

    monkeypatch.setattr("job_page_finder.runtime.create_deepseek_llm", boom)
    result = await run_task(
        {
            "version": "v1",
            "task_id": "log-config",
            "type": "find_job_page",
            "payload": {"company_url": "https://example.com"},
        }
    )

    assert saw_started_before_assembly is True
    assert result.error is not None
    assert result.error.code == "CONFIGURATION_ERROR"
    assert result.task_id == "log-config"
    _assert_single_started_then_finished(
        caplog,
        task_id="log-config",
        task_type="find_job_page",
        error_code="CONFIGURATION_ERROR",
    )


@pytest.mark.asyncio
async def test_run_task_does_not_duplicate_lifecycle_logs(monkeypatch, caplog) -> None:
    """Keep a single started/finished pair when run_task delegates to the runner."""
    caplog.set_level(logging.INFO, logger="job_page_finder")

    async def fake_find(finder_input: JobPageFinderInput) -> JobPageFinderResult:
        return JobPageFinderResult(
            success=True,
            job_page_url="https://example.com/careers",
            job_title="Senior Backend Engineer",
            evidence="Senior Backend Engineer",
            steps=1,
        )

    monkeypatch.setattr(
        "job_page_finder.runtime.build_application",
        lambda *args, **kwargs: TaskRunner(fake_find),
    )
    result = await run_task(
        {
            "version": "v1",
            "task_id": "log-success",
            "type": "find_job_page",
            "payload": {"company_url": "https://example.com"},
        }
    )
    events = _task_log_events(caplog)

    assert result.status == "succeeded"
    assert [kind for kind, _ in events] == ["started", "finished"]
    assert events[0][1] == "task started task_id=log-success task_type=find_job_page"
    assert events[1][1].startswith("task finished task_id=log-success task_type=find_job_page status=succeeded")
