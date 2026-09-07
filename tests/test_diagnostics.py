import asyncio
import base64
import io
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from job_page_finder.cli import EXIT_SUCCESS, main
from job_page_finder.diagnostics import DiagnosticWriter, serializable
from job_page_finder.finder import JobPageFinder
from job_page_finder.models import AgentDecision, JobPageFinderInput, JobPageFinderResult
from job_page_finder.runner import TaskRunner
from job_page_finder.runtime import RuntimeConfig, run_task


class FakeDom:
    _root = object()
    selector_map: dict[int, object] = {}

    def __init__(self, text: str) -> None:
        self.text = text

    def llm_representation(self) -> str:
        return self.text


class FakeBrowser:
    def __init__(self, dom: str, screenshot: str, *, action_snapshot_delay: float = 0, state_delay: float = 0) -> None:
        self.state = SimpleNamespace(
            url="https://example.com/careers",
            title="Example careers",
            dom_state=FakeDom(dom),
            screenshot=screenshot,
            page_info=SimpleNamespace(
                pixels_above=0,
                pixels_below=0,
                scroll_x=0,
                scroll_y=0,
                viewport_width=10,
                viewport_height=10,
            ),
        )
        self.screenshot_options: list[bool] = []
        self.action_snapshot_delay = action_snapshot_delay
        self.state_delay = state_delay

    async def start(self) -> None:
        pass

    async def navigate_to(self, url: str) -> None:
        pass

    async def get_browser_state_summary(self, *, include_screenshot: bool) -> Any:
        self.screenshot_options.append(include_screenshot)
        if self.state_delay:
            await asyncio.sleep(self.state_delay)
        if len(self.screenshot_options) > 1 and self.action_snapshot_delay and include_screenshot:
            await asyncio.sleep(self.action_snapshot_delay)
        return self.state

    async def get_current_page_url(self) -> str:
        return self.state.url

    async def kill(self) -> None:
        pass


class FakeLlm:
    def __init__(self, decision: dict[str, object]) -> None:
        self.decision = decision
        self.calls: list[list[Any]] = []

    async def ainvoke(self, messages: list[Any], **kwargs: Any) -> Any:
        self.calls.append(messages)
        return SimpleNamespace(completion=AgentDecision.model_validate({"decision": self.decision}))


class FakeTools:
    async def scroll(self, **kwargs: Any) -> Any:
        page_info = kwargs["browser_session"].state.page_info
        page_info.scroll_y += 10
        page_info.pixels_above += 10
        return SimpleNamespace(error=None, extracted_content="Scrolled")


class SlowTools(FakeTools):
    async def scroll(self, **kwargs: Any) -> Any:
        await asyncio.sleep(0.05)
        return await super().scroll(**kwargs)


def png() -> str:
    output = io.BytesIO()
    Image.new("RGB", (10, 10), "white").save(output, format="PNG")
    return base64.b64encode(output.getvalue()).decode("ascii")


def request(task_id: str = "diagnostic-task", *, max_steps: int = 1) -> dict[str, object]:
    return {
        "version": "v1",
        "task_id": task_id,
        "type": "find_job_page",
        "payload": {"company_url": "https://example.com", "max_steps": max_steps},
    }


def artifact_kinds(run_dir: Path) -> set[str]:
    return {item["kind"] for item in json.loads((run_dir / "manifest.json").read_text())["artifacts"]}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("level", "required", "forbidden"),
    [
        ("basic", {"result"}, {"model_messages", "page_input", "screenshot", "raw_page_snapshot"}),
        (
            "diagnostic",
            {"result", "model_messages", "model_response", "parsed_decision", "page_input", "screenshot"},
            {"raw_page_snapshot"},
        ),
        ("raw", {"result", "model_messages", "page_input", "raw_page_snapshot"}, set()),
    ],
)
async def test_diagnostic_levels_gate_artifacts(
    tmp_path: Path, level: str, required: set[str], forbidden: set[str]
) -> None:
    browser = FakeBrowser("<div>Senior Engineer</div>", png())
    llm = FakeLlm({"type": "done", "job_title": "Senior Engineer", "evidence": "Senior Engineer"})
    finder = JobPageFinder(llm=llm, browser_factory=lambda: browser, tools=FakeTools())

    result = await run_task(
        request(), finder=finder, config=RuntimeConfig(diagnostics_root=tmp_path, diagnostics_level=level)
    )

    assert result.status == "succeeded"
    run_dir = next(tmp_path.iterdir())
    manifest = json.loads((run_dir / "manifest.json").read_text())
    events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text().splitlines()]
    assert manifest["run_id"] and manifest["task_id"] == "diagnostic-task"
    assert manifest["status"] == "succeeded" and manifest["redaction_applied"] is False
    assert {event["run_id"] for event in events} == {manifest["run_id"]}
    assert {"task_started", "step_started", "model_call_started", "run_finished"} <= {
        event["event"] for event in events
    }
    kinds = artifact_kinds(run_dir)
    assert required <= kinds
    assert not kinds.intersection(forbidden)


@pytest.mark.asyncio
async def test_screenshot_capture_does_not_change_nonvision_model_input(tmp_path: Path) -> None:
    inputs: list[Any] = []
    for enabled in (False, True):
        browser = FakeBrowser("<div>Senior Engineer</div>", png())
        llm = FakeLlm({"type": "done", "job_title": "Senior Engineer", "evidence": "Senior Engineer"})
        finder = JobPageFinder(llm=llm, browser_factory=lambda: browser, tools=FakeTools(), use_vision=False)
        result = await run_task(
            request(f"screenshots-{enabled}"),
            finder=finder,
            config=RuntimeConfig(
                diagnostics_root=tmp_path / str(enabled),
                diagnostics_level="diagnostic",
                diagnostics_screenshots=enabled,
            ),
        )
        assert result.status == "succeeded"
        inputs.append(llm.calls[0][-1].content)
        assert browser.screenshot_options == [enabled]
    assert inputs[0] == inputs[1]


@pytest.mark.asyncio
async def test_raw_records_post_action_snapshot_and_concurrent_runs_are_isolated(tmp_path: Path) -> None:
    async def one(task_id: str) -> JobPageFinderResult:
        browser = FakeBrowser("<div>Careers</div>", png())
        finder = JobPageFinder(
            llm=FakeLlm({"type": "scroll", "direction": "down"}),
            browser_factory=lambda: browser,
            tools=FakeTools(),
        )
        return await run_task(
            request(task_id), finder=finder, config=RuntimeConfig(diagnostics_root=tmp_path, diagnostics_level="raw")
        )

    first, second = await asyncio.gather(one("concurrent-a"), one("concurrent-b"))
    assert first.status == second.status == "failed"
    runs = [path for path in tmp_path.iterdir() if path.is_dir()]
    assert len(runs) == 2 and len({run.name for run in runs}) == 2
    manifests = [json.loads((run / "manifest.json").read_text()) for run in runs]
    assert {manifest["task_id"] for manifest in manifests} == {"concurrent-a", "concurrent-b"}
    assert all("raw_action_snapshot" in artifact_kinds(run) for run in runs)


@pytest.mark.asyncio
async def test_raw_action_snapshot_timeout_does_not_change_action_result(tmp_path: Path) -> None:
    browser = FakeBrowser("<div>Careers</div>", png(), action_snapshot_delay=0.1)
    finder = JobPageFinder(
        llm=FakeLlm({"type": "scroll", "direction": "down"}),
        browser_factory=lambda: browser,
        tools=FakeTools(),
        step_timeout=0.01,
    )

    result = await run_task(
        request(max_steps=1), finder=finder, config=RuntimeConfig(diagnostics_root=tmp_path, diagnostics_level="raw")
    )

    assert result.error is not None and result.error.code == "MAX_STEPS_REACHED"
    run_dir = next(path for path in tmp_path.iterdir() if path.is_dir())
    events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text().splitlines()]
    assert any(event["event"] == "raw_action_snapshot_failed" for event in events)


@pytest.mark.asyncio
async def test_step_start_is_recorded_before_browser_state_timeout(tmp_path: Path) -> None:
    browser = FakeBrowser("<div>Careers</div>", png(), state_delay=0.05)
    finder = JobPageFinder(
        llm=FakeLlm({"type": "done", "job_title": "Careers", "evidence": "Careers"}),
        browser_factory=lambda: browser,
        tools=FakeTools(),
        step_timeout=0.01,
        max_consecutive_failures=1,
    )

    result = await run_task(request(), finder=finder, config=RuntimeConfig(diagnostics_root=tmp_path))

    assert result.error is not None and result.error.code == "STEP_TIMEOUT"
    run_dir = next(path for path in tmp_path.iterdir() if path.is_dir())
    events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text().splitlines()]
    assert [event["event"] for event in events if event.get("step_id") == "step-1"] == [
        "step_started",
        "step_finished",
    ]


@pytest.mark.asyncio
async def test_action_timeout_has_matched_action_and_step_events(tmp_path: Path) -> None:
    finder = JobPageFinder(
        llm=FakeLlm({"type": "scroll", "direction": "down"}),
        browser_factory=lambda: FakeBrowser("<div>Careers</div>", png()),
        tools=SlowTools(),
        step_timeout=0.01,
        max_consecutive_failures=1,
    )

    result = await run_task(request(), finder=finder, config=RuntimeConfig(diagnostics_root=tmp_path))

    assert result.error is not None and result.error.code == "STEP_TIMEOUT"
    run_dir = next(path for path in tmp_path.iterdir() if path.is_dir())
    events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text().splitlines()]
    assert [event["event"] for event in events if event.get("step_id") == "step-1"] == [
        "step_started",
        "page_observed",
        "model_call_started",
        "model_call_finished",
        "action_selected",
        "action_finished",
        "step_finished",
    ]
    assert [event["status"] for event in events if event["event"] in {"action_finished", "step_finished"}] == [
        "timed_out",
        "timed_out",
    ]


@pytest.mark.asyncio
async def test_capacity_omits_artifacts_and_preserves_manifest_summary(tmp_path: Path) -> None:
    writer = DiagnosticWriter(
        root=tmp_path,
        level="diagnostic",
        task_id="limited",
        task_type="find_job_page",
        capture_screenshots=False,
        max_runs=5,
        retention_days=7,
        max_run_bytes=9_000,
        max_total_bytes=20_000,
    )

    assert writer.artifact("large", "x" * 20_000) is None
    writer.finish({"status": "succeeded"})

    manifest = json.loads((writer.run_dir / "manifest.json").read_text())
    events = [json.loads(line) for line in (writer.run_dir / "events.jsonl").read_text().splitlines()]
    assert manifest["diagnostic_incomplete"] is True
    assert manifest["omitted_artifacts"][0]["kind"] == "large"
    assert any(event["event"] == "artifact_omitted" for event in events)
    assert any(item["kind"] == "result" for item in manifest["artifacts"])


def test_tiny_capacity_always_writes_final_result(tmp_path: Path) -> None:
    writer = DiagnosticWriter(
        root=tmp_path,
        level="basic",
        task_id="tiny-budget",
        task_type="find_job_page",
        capture_screenshots=False,
        max_runs=5,
        retention_days=7,
        max_run_bytes=1,
        max_total_bytes=1,
    )

    writer.finish({"status": "succeeded"})

    manifest = json.loads((writer.run_dir / "manifest.json").read_text())
    assert manifest["diagnostic_incomplete"] is True
    assert manifest["summary_budget_exceeded"] is True
    assert artifact_kinds(writer.run_dir) == {"result"}
    assert "omitted_artifacts" not in manifest


@pytest.mark.asyncio
async def test_total_capacity_is_shared_and_artifact_files_are_private(tmp_path: Path) -> None:
    def create(task_id: str) -> DiagnosticWriter:
        return DiagnosticWriter(
            root=tmp_path,
            level="diagnostic",
            task_id=task_id,
            task_type="find_job_page",
            capture_screenshots=False,
            max_runs=5,
            retention_days=7,
            max_run_bytes=20_000,
            max_total_bytes=10_000,
        )

    first = create("first")
    second = create("second")
    assert first.artifact("small", "x" * 500) is not None
    assert second.artifact("small", "x" * 500) is None
    artifact = next((first.run_dir / "artifacts").iterdir())
    assert artifact.stat().st_mode & 0o777 == 0o600


def test_serialization_omits_unavailable_sensitive_or_hidden_sdk_fields() -> None:
    assert serializable({"content": "visible", "thinking": "hidden", "headers": {"x": "secret"}}) == {
        "content": "visible"
    }


class SlowDiagnosticWriter(DiagnosticWriter):
    def artifact(self, *args: Any, **kwargs: Any) -> str | None:
        time.sleep(0.03)
        return super().artifact(*args, **kwargs)


class SlowActionEventDiagnosticWriter(DiagnosticWriter):
    def event(self, name: str, **fields: Any) -> None:
        if name == "action_selected":
            time.sleep(0.03)
        super().event(name, **fields)


@pytest.mark.asyncio
async def test_slow_diagnostic_serialization_does_not_consume_step_timeout(tmp_path: Path) -> None:
    browser = FakeBrowser("<div>Senior Engineer</div>", png())
    finder = JobPageFinder(
        llm=FakeLlm({"type": "done", "job_title": "Senior Engineer", "evidence": "Senior Engineer"}),
        browser_factory=lambda: browser,
        tools=FakeTools(),
        step_timeout=0.01,
    )
    writer = SlowDiagnosticWriter(
        root=tmp_path,
        level="diagnostic",
        task_id="slow-writer",
        task_type="find_job_page",
        capture_screenshots=False,
        max_runs=5,
        retention_days=7,
    )

    result = await TaskRunner(finder).run(request("slow-writer"), diagnostics=writer)

    assert result.status == "succeeded"


@pytest.mark.asyncio
async def test_slow_action_selection_event_does_not_consume_step_timeout(tmp_path: Path) -> None:
    browser = FakeBrowser("<div>Careers</div>", png())
    finder = JobPageFinder(
        llm=FakeLlm({"type": "scroll", "direction": "down"}),
        browser_factory=lambda: browser,
        tools=FakeTools(),
        step_timeout=0.01,
    )
    writer = SlowActionEventDiagnosticWriter(
        root=tmp_path,
        level="basic",
        task_id="slow-action-event",
        task_type="find_job_page",
        capture_screenshots=False,
        max_runs=5,
        retention_days=7,
    )

    result = await TaskRunner(finder).run(request("slow-action-event", max_steps=1), diagnostics=writer)

    assert result.error is not None and result.error.code == "MAX_STEPS_REACHED"


def test_cleanup_failure_is_recorded_in_manifest_and_events(monkeypatch, tmp_path: Path) -> None:
    writer = DiagnosticWriter(
        root=tmp_path,
        level="basic",
        task_id="cleanup-failure",
        task_type="find_job_page",
        capture_screenshots=False,
        max_runs=5,
        retention_days=7,
    )

    def fail_cleanup(**kwargs: Any) -> list[Path]:
        raise OSError("cleanup denied")

    monkeypatch.setattr(writer, "_cleanup", fail_cleanup)
    writer.finish({"status": "succeeded"})

    manifest = json.loads((writer.run_dir / "manifest.json").read_text())
    events = [json.loads(line) for line in (writer.run_dir / "events.jsonl").read_text().splitlines()]
    assert "cleanup denied" in manifest["diagnostic_failure"]
    assert any(event["event"] == "diagnostic_cleanup_failed" for event in events)


@pytest.mark.asyncio
async def test_artifact_write_failure_does_not_change_task_result(monkeypatch, tmp_path: Path) -> None:
    async def successful(_: JobPageFinderInput) -> JobPageFinderResult:
        return JobPageFinderResult(
            success=True,
            job_page_url="https://example.com/careers",
            job_title="Engineer",
            evidence="Engineer",
            steps=1,
        )

    writer = DiagnosticWriter(
        root=tmp_path,
        level="basic",
        task_id="write-failure",
        task_type="find_job_page",
        capture_screenshots=False,
        max_runs=5,
        retention_days=7,
    )
    original_atomic_write = writer._atomic_write

    def fail_artifact_writes(destination: Path, data: bytes, temporary: Path | None = None) -> None:
        if destination.parent.name == "artifacts":
            raise OSError("artifact write denied")
        original_atomic_write(destination, data, temporary)

    monkeypatch.setattr(writer, "_atomic_write", fail_artifact_writes)
    result = await TaskRunner(successful).run(request("write-failure"), diagnostics=writer)

    assert result.status == "succeeded"
    manifest = json.loads((writer.run_dir / "manifest.json").read_text())
    assert "artifact write denied" in manifest["diagnostic_failure"]


@pytest.mark.asyncio
async def test_capacity_and_writer_creation_failure_do_not_change_task_result(tmp_path: Path) -> None:
    async def successful(_: JobPageFinderInput) -> JobPageFinderResult:
        return JobPageFinderResult(
            success=True,
            job_page_url="https://example.com/careers",
            job_title="Engineer",
            evidence="Engineer",
            steps=1,
        )

    runner = TaskRunner(successful)
    config = RuntimeConfig(diagnostics_root=tmp_path, diagnostics_max_runs=1)
    assert (await run_task(request("capacity-one"), runner=runner, config=config)).status == "succeeded"
    assert (await run_task(request("capacity-two"), runner=runner, config=config)).status == "succeeded"
    assert len([path for path in tmp_path.iterdir() if path.is_dir()]) == 1

    blocked_root = tmp_path / "not-a-directory"
    blocked_root.write_text("file")
    result = await run_task(request("disabled"), runner=runner, config=RuntimeConfig(diagnostics_root=blocked_root))
    assert result.status == "succeeded"

    retained_root = tmp_path / "retained"
    stale_id = "a" * 32
    stale = retained_root / f"20000101T000000000000Z_{stale_id}"
    stale.mkdir(parents=True)
    (stale / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": stale_id,
                "task_id": "stale",
                "task_type": "find_job_page",
                "diagnostic_level": "basic",
                "redaction_applied": False,
                "status": "succeeded",
                "artifacts": [],
            }
        )
    )
    os.utime(stale, (time.time() - 172_800, time.time() - 172_800))
    retained = await run_task(
        request("retained"),
        runner=runner,
        config=RuntimeConfig(diagnostics_root=retained_root, diagnostics_max_runs=1, diagnostics_retention_days=1),
    )
    assert retained.status == "succeeded"
    assert len([path for path in retained_root.iterdir() if path.is_dir()]) == 1


@pytest.mark.asyncio
async def test_cleanup_preserves_unmanaged_or_active_directories(tmp_path: Path) -> None:
    async def successful(_: JobPageFinderInput) -> JobPageFinderResult:
        return JobPageFinderResult(
            success=True,
            job_page_url="https://example.com/careers",
            job_title="Engineer",
            evidence="Engineer",
            steps=1,
        )

    unrelated = tmp_path / f"20000101T000000000000Z_{'b' * 32}"
    unrelated.mkdir()
    (unrelated / "manifest.json").write_text(
        json.dumps({"run_id": "b" * 32, "diagnostic_level": "basic", "status": "succeeded"})
    )
    active_id = "c" * 32
    active = tmp_path / f"20000101T000000000000Z_{active_id}"
    active.mkdir()
    (active / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": active_id,
                "task_id": "active",
                "task_type": "find_job_page",
                "diagnostic_level": "basic",
                "redaction_applied": False,
                "status": "running",
                "artifacts": [],
            }
        )
    )

    result = await run_task(
        request("new"),
        runner=TaskRunner(successful),
        config=RuntimeConfig(diagnostics_root=tmp_path, diagnostics_max_runs=2, diagnostics_retention_days=0),
    )

    assert result.status == "succeeded"
    assert unrelated.is_dir()
    assert active.is_dir()


@pytest.mark.asyncio
async def test_diagnostic_saves_actual_annotated_screenshot_and_coordinates(tmp_path: Path) -> None:
    browser = FakeBrowser("<div>Careers</div>", png())
    node = SimpleNamespace(
        absolute_position=SimpleNamespace(x=1, y=1, width=5, height=5),
        is_visible=True,
        ax_node=None,
        children=[],
        attributes={},
        get_meaningful_text_for_llm=lambda: "",
    )
    browser.state.dom_state.selector_map = {7: node}
    llm = FakeLlm({"type": "scroll", "direction": "down"})
    finder = JobPageFinder(
        llm=llm,
        browser_factory=lambda: browser,
        tools=FakeTools(),
        use_vision=True,
    )

    result = await run_task(
        request(max_steps=1),
        finder=finder,
        config=RuntimeConfig(diagnostics_root=tmp_path, diagnostics_level="diagnostic"),
    )

    assert result.status == "failed"
    run_dir = next(tmp_path.iterdir())
    assert {"annotated_screenshot", "visual_candidates"} <= artifact_kinds(run_dir)
    candidate = next((run_dir / "artifacts").glob("*_visual_candidates.json"))
    assert json.loads(candidate.read_text()) == [{"index": 7, "coordinates": [1, 1, 6, 6]}]
    assert llm.calls[0][-1].content[-1].type == "image_url"


def test_cli_diagnostic_options_keep_stdout_json(monkeypatch, capsys, tmp_path: Path) -> None:
    captured: dict[str, RuntimeConfig] = {}

    async def fake_run_task(payload: object, *, config: RuntimeConfig) -> Any:
        captured["config"] = config
        return await TaskRunner(
            lambda _: asyncio.sleep(
                0,
                result=JobPageFinderResult(
                    success=True,
                    job_page_url="https://example.com/careers",
                    job_title="Engineer",
                    evidence="Engineer",
                    steps=1,
                ),
            )
        ).run(payload)

    monkeypatch.setattr("job_page_finder.cli.run_task", fake_run_task)
    assert (
        main(
            [
                "find-job-page",
                "https://example.com",
                "--diagnostics-level",
                "raw",
                "--diagnostics-root",
                str(tmp_path),
                "--diagnostics-max-run-bytes",
                "1234",
                "--diagnostics-max-total-bytes",
                "5678",
            ]
        )
        == EXIT_SUCCESS
    )
    stdout = json.loads(capsys.readouterr().out)
    assert stdout["status"] == "succeeded"
    assert captured["config"].diagnostics_level == "raw"
    assert captured["config"].diagnostics_root == tmp_path
    assert captured["config"].diagnostics_max_run_bytes == 1234
    assert captured["config"].diagnostics_max_total_bytes == 5678


def test_cli_invalid_diagnostic_limits_are_structured_json(capsys) -> None:
    assert main(["find-job-page", "https://example.com", "--diagnostics-max-runs", "0"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["code"] == "INVALID_TASK"

    assert main(["find-job-page", "https://example.com", "--diagnostics-max-run-bytes", "0"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["code"] == "INVALID_TASK"

    assert main(["find-job-page", "https://example.com", "--diagnostics-retention-days", "-1"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["code"] == "INVALID_TASK"


@pytest.mark.asyncio
async def test_cancelled_run_is_marked_aborted_and_reraises(tmp_path: Path) -> None:
    started = asyncio.Event()

    async def blocked(_: JobPageFinderInput) -> JobPageFinderResult:
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    writer = DiagnosticWriter(
        root=tmp_path,
        level="basic",
        task_id="cancelled",
        task_type="find_job_page",
        capture_screenshots=False,
        max_runs=5,
        retention_days=7,
    )
    task = asyncio.create_task(TaskRunner(blocked).run(request("cancelled"), diagnostics=writer))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    manifest = json.loads((writer.run_dir / "manifest.json").read_text())
    assert manifest["status"] == "aborted"
