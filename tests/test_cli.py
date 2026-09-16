import io
import json
import sys
from pathlib import Path

import pytest

from job_page_finder.cli import (
    EXIT_CONFIGURATION_ERROR,
    EXIT_INVALID_INPUT,
    EXIT_SUCCESS,
    EXIT_TASK_FAILED,
    main,
)
from job_page_finder.cli import _iter_jsonl_lines as _iter_jsonl_lines
from job_page_finder.contracts import (
    FindJobPageFailure,
    FindJobPageRequest,
    FindJobPageResult,
    FindJobPageSuccess,
    JobEvidence,
)
from job_page_finder.evaluation import EvaluationCase
from job_page_finder.runtime import RuntimeConfig
from job_page_finder.task_protocol import FindJobPageTaskOutput as FindJobPageOutput
from job_page_finder.task_protocol import TaskMetadata, TaskResult


def succeeded_result(*, job_page_url: str, job_title: str, evidence: str, steps: int) -> FindJobPageSuccess:
    return FindJobPageSuccess(
        status="succeeded",
        job_page_url=job_page_url,
        job_title=job_title,
        evidence=JobEvidence(quote=evidence, source_url=job_page_url),
        steps=steps,
    )


def failed_result(*, code: str, message: str, steps: int) -> FindJobPageFailure:
    return FindJobPageFailure(status="failed", code=code, message=message, retryable=True, steps=steps)


def install_fake_finder(monkeypatch, result: FindJobPageResult | Exception):
    async def fake_find(finder_input: FindJobPageRequest) -> FindJobPageResult:
        if isinstance(result, Exception):
            raise result
        return result

    class Finder:
        async def find(self, finder_input: FindJobPageRequest, *, events: object = None) -> FindJobPageResult:
            return await fake_find(finder_input)

    def fake_build(settings=None, **kwargs):
        from job_page_finder.runtime import build_application

        return build_application(settings, finder=Finder(), **kwargs)

    monkeypatch.setattr("job_page_finder.cli.build_application", fake_build)


def parse_stdout(capsys) -> tuple[dict, str]:
    captured = capsys.readouterr()
    return json.loads(captured.out), captured.err


def write_task(
    tmp_path: Path,
    *,
    company_url: str = "https://example.com",
    max_steps: int = 8,
    task_id: str = "cli-task",
) -> str:
    path = tmp_path / "task.jsonl"
    path.write_text(
        json.dumps(
            {
                "version": "v1",
                "task_id": task_id,
                "type": "find_job_page",
                "payload": {"company_url": company_url, "max_steps": max_steps},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return str(path)


def test_run_prints_json_and_exits_zero(monkeypatch, capsys, tmp_path: Path) -> None:
    """Accept JSONL run input and print a succeeded JSON result."""
    install_fake_finder(
        monkeypatch,
        succeeded_result(
            job_page_url="https://example.com/careers",
            job_title="Senior Backend Engineer",
            evidence="Senior Backend Engineer",
            steps=3,
        ),
    )

    exit_code = main(["run", write_task(tmp_path, max_steps=4)])
    payload, stderr = parse_stdout(capsys)

    assert exit_code == EXIT_SUCCESS
    assert payload["status"] == "succeeded"
    assert payload["type"] == "find_job_page"
    assert payload["output"]["job_title"] == "Senior Backend Engineer"
    assert payload["error"] is None
    assert isinstance(payload["metadata"]["duration_ms"], int)
    assert not stderr or "task_id=" in stderr


def test_cli_default_diagnostics_root_uses_test_tmp_path(
    monkeypatch, capsys, tmp_path: Path, isolate_default_diagnostics_root
) -> None:
    install_fake_finder(
        monkeypatch,
        succeeded_result(
            job_page_url="https://example.com/careers",
            job_title="Senior Backend Engineer",
            evidence="Senior Backend Engineer",
            steps=1,
        ),
    )

    assert main(["run", write_task(tmp_path)]) == EXIT_SUCCESS
    parse_stdout(capsys)
    assert len([path for path in isolate_default_diagnostics_root.iterdir() if path.is_dir()]) == 1


def test_run_json_file_uses_the_same_result_contract(monkeypatch, capsys, tmp_path: Path) -> None:
    """Read a JSON task file and print the same unified result envelope."""
    install_fake_finder(
        monkeypatch,
        succeeded_result(
            job_page_url="https://example.com/careers",
            job_title="Senior Backend Engineer",
            evidence="Senior Backend Engineer",
            steps=2,
        ),
    )
    task_file = tmp_path / "task.json"
    task_file.write_text(
        json.dumps(
            {
                "version": "v1",
                "task_id": "file-task",
                "type": "find_job_page",
                "payload": {"company_url": "https://example.com", "max_steps": 5},
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(["run", str(task_file)])
    payload, _ = parse_stdout(capsys)

    assert exit_code == EXIT_SUCCESS
    assert payload["task_id"] == "file-task"
    assert payload["status"] == "succeeded"
    assert payload["output"]["steps"] == 2


def test_invalid_json_file_exits_nonzero_with_structured_error(capsys, tmp_path: Path) -> None:
    """Reject malformed task files before execution and keep stdout parseable."""
    task_file = tmp_path / "bad.json"
    task_file.write_text("{not json", encoding="utf-8")

    exit_code = main(["run", str(task_file)])
    payload, stderr = parse_stdout(capsys)

    assert exit_code == EXIT_INVALID_INPUT
    assert payload["status"] == "failed"
    assert payload["error"]["code"] == "INVALID_TASK"
    assert "Traceback" not in json.dumps(payload)
    assert "Traceback" not in stderr


def test_unknown_task_type_exits_nonzero(monkeypatch, capsys, tmp_path: Path) -> None:
    """Map an unknown task type to a structured CLI failure."""
    install_fake_finder(
        monkeypatch,
        succeeded_result(job_page_url="https://example.com/x", job_title="x", evidence="x", steps=1),
    )
    task_file = tmp_path / "unknown.json"
    task_file.write_text(
        json.dumps({"version": "v1", "type": "extract_jobs", "payload": {"company_url": "https://example.com"}}),
        encoding="utf-8",
    )

    exit_code = main(["run", str(task_file)])
    payload, _ = parse_stdout(capsys)

    assert exit_code == EXIT_INVALID_INPUT
    assert payload["error"]["code"] == "UNSUPPORTED_TASK_TYPE"


def test_task_failure_uses_exit_code_one(monkeypatch, capsys, tmp_path: Path) -> None:
    """Use a non-zero task failure exit code when the finder returns a domain error."""
    install_fake_finder(
        monkeypatch,
        failed_result(
            code="MAX_STEPS_REACHED", message="Maximum steps reached without finding a specific job", steps=8
        ),
    )

    exit_code = main(["run", write_task(tmp_path)])
    payload, _ = parse_stdout(capsys)

    assert exit_code == EXIT_TASK_FAILED
    assert payload["status"] == "failed"
    assert payload["error"]["code"] == "MAX_STEPS_REACHED"
    assert payload["output"] is None


def test_configuration_error_uses_exit_code_three(monkeypatch, capsys, tmp_path: Path) -> None:
    """Surface composition failures as CONFIGURATION_ERROR with a dedicated exit code."""

    def boom(**kwargs):
        raise RuntimeError("DEEPSEEK_API_KEY is not configured")

    monkeypatch.setattr("job_page_finder.runtime.create_deepseek_llm", boom)
    exit_code = main(["run", write_task(tmp_path)])
    payload, _ = parse_stdout(capsys)

    assert exit_code == EXIT_CONFIGURATION_ERROR
    assert payload["error"]["code"] == "CONFIGURATION_ERROR"


def test_unreadable_task_file_exits_nonzero_with_structured_error(monkeypatch, capsys, tmp_path: Path) -> None:
    """Keep stdout parseable when the task file cannot be decoded or read."""
    utf16_file = tmp_path / "utf16.json"
    utf16_file.write_bytes('{"version": "v1"}'.encode("utf-16"))
    exit_code = main(["run", str(utf16_file)])
    payload, stderr = parse_stdout(capsys)

    assert exit_code == EXIT_INVALID_INPUT
    assert payload["error"]["code"] == "INVALID_TASK"
    assert "Traceback" not in json.dumps(payload)
    assert "Traceback" not in stderr

    blocked = tmp_path / "blocked.json"
    blocked.write_text("{}", encoding="utf-8")
    original_open = Path.open

    def fake_open(self, *args, **kwargs):
        if self == blocked:
            raise PermissionError("denied")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fake_open)
    exit_code = main(["run", str(blocked)])
    payload, stderr = parse_stdout(capsys)

    assert exit_code == EXIT_INVALID_INPUT
    assert payload["error"]["code"] == "INVALID_TASK"
    assert "Traceback" not in json.dumps(payload)
    assert "Traceback" not in stderr


def test_invalid_payload_is_not_masked_by_configuration_error(monkeypatch, capsys, tmp_path: Path) -> None:
    """Reject illegal CLI payload before composition so the exit code stays 2."""

    def boom(**kwargs):
        raise RuntimeError("DEEPSEEK_API_KEY is not configured")

    monkeypatch.setattr("job_page_finder.runtime.create_deepseek_llm", boom)
    exit_code = main(["run", write_task(tmp_path, company_url="not-a-url")])
    payload, _ = parse_stdout(capsys)

    assert exit_code == EXIT_INVALID_INPUT
    assert payload["error"]["code"] == "INVALID_TASK"


def test_usage_error_prints_json_on_stdout(capsys) -> None:
    """Keep usage errors as parseable JSON on stdout."""
    exit_code = main([])
    payload, _ = parse_stdout(capsys)

    assert exit_code == EXIT_INVALID_INPUT
    assert payload["error"]["code"] == "INVALID_TASK"
    assert payload["status"] == "failed"


def _task_line(task_id: str) -> str:
    return json.dumps(
        {
            "version": "v1",
            "task_id": task_id,
            "type": "find_job_page",
            "payload": {"company_url": "https://example.com"},
        }
    )


def test_run_jsonl_emits_one_result_per_nonempty_line(monkeypatch, capsys, tmp_path: Path) -> None:
    install_fake_finder(
        monkeypatch,
        succeeded_result(
            job_page_url="https://example.com/careers", job_title="Engineer", evidence="Engineer", steps=1
        ),
    )
    task_file = tmp_path / "tasks.jsonl"
    task_file.write_text("\n".join(["", _task_line("one"), "", _task_line("two"), ""]) + "\n", encoding="utf-8")

    exit_code = main(["run", str(task_file)])
    captured = capsys.readouterr()
    lines = [json.loads(line) for line in captured.out.splitlines() if line.strip()]

    assert exit_code == EXIT_SUCCESS
    assert [line["task_id"] for line in lines] == ["one", "two"]
    assert all(line["status"] == "succeeded" for line in lines)


def test_run_jsonl_continues_after_invalid_lines(monkeypatch, capsys, tmp_path: Path) -> None:
    install_fake_finder(
        monkeypatch,
        succeeded_result(
            job_page_url="https://example.com/careers", job_title="Engineer", evidence="Engineer", steps=1
        ),
    )
    task_file = tmp_path / "mixed.jsonl"
    task_file.write_text(
        "\n".join(["{not json", _task_line("ok"), "[]", _task_line("still-ok")]) + "\n",
        encoding="utf-8",
    )

    exit_code = main(["run", str(task_file)])
    captured = capsys.readouterr()
    lines = [json.loads(line) for line in captured.out.splitlines() if line.strip()]

    assert exit_code == EXIT_INVALID_INPUT
    assert len(lines) == 4
    assert lines[0]["error"]["code"] == "INVALID_TASK"
    assert lines[1]["task_id"] == "ok"
    assert lines[2]["error"]["code"] == "INVALID_TASK"
    assert lines[3]["task_id"] == "still-ok"


def test_run_reads_stdin_and_dash(monkeypatch, capsys) -> None:
    install_fake_finder(
        monkeypatch,
        succeeded_result(
            job_page_url="https://example.com/careers", job_title="Engineer", evidence="Engineer", steps=1
        ),
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO(_task_line("stdin-task") + "\n"))
    exit_code = main(["run"])
    captured = capsys.readouterr()
    lines = [json.loads(line) for line in captured.out.splitlines() if line.strip()]
    assert exit_code == EXIT_SUCCESS
    assert lines[0]["task_id"] == "stdin-task"

    monkeypatch.setattr(sys, "stdin", io.StringIO(_task_line("dash-task") + "\n"))
    exit_code = main(["run", "-"])
    captured = capsys.readouterr()
    lines = [json.loads(line) for line in captured.out.splitlines() if line.strip()]
    assert exit_code == EXIT_SUCCESS
    assert lines[0]["task_id"] == "dash-task"


def test_stdin_read_errors_are_structured_invalid_tasks(monkeypatch) -> None:
    class BrokenInput:
        def __iter__(self):
            raise UnicodeDecodeError("utf-8", b"\\xff", 0, 1, "invalid start byte")

    monkeypatch.setattr(sys, "stdin", BrokenInput())
    values = list(_iter_jsonl_lines(None))

    assert len(values) == 1
    assert isinstance(values[0], TaskResult)
    assert values[0].error is not None and values[0].error.code == "INVALID_TASK"


def test_run_jsonl_mixed_task_failure_uses_exit_code_one(monkeypatch, capsys, tmp_path: Path) -> None:
    calls = {"n": 0}

    async def fake_find(finder_input: FindJobPageRequest) -> FindJobPageResult:
        calls["n"] += 1
        if calls["n"] == 1:
            return succeeded_result(
                job_page_url="https://example.com/careers", job_title="Engineer", evidence="Engineer", steps=1
            )
        return failed_result(code="MAX_STEPS_REACHED", message="failed", steps=1)

    class Finder:
        async def find(self, finder_input: FindJobPageRequest, *, events: object = None) -> FindJobPageResult:
            return await fake_find(finder_input)

    def fake_build(settings=None, **kwargs):
        from job_page_finder.runtime import build_application

        return build_application(settings, finder=Finder(), **kwargs)

    monkeypatch.setattr("job_page_finder.cli.build_application", fake_build)
    task_file = tmp_path / "mixed.jsonl"
    task_file.write_text(_task_line("ok") + "\n" + _task_line("fail") + "\n", encoding="utf-8")

    exit_code = main(["run", str(task_file)])
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert exit_code == EXIT_TASK_FAILED
    assert [line["status"] for line in lines] == ["succeeded", "failed"]


def test_evaluate_generate_prints_manifest(monkeypatch, capsys, tmp_path: Path) -> None:
    captured: dict = {}

    def fake_generate(db_path, output_path, **kwargs):
        captured.update({"db_path": db_path, "output_path": output_path, **kwargs})
        return {"source": "corpweb", "selected_count": 12}

    monkeypatch.setattr("job_page_finder.cli.generate_corpweb_dataset", fake_generate)
    exit_code = main(
        [
            "evaluate",
            "generate",
            "--db",
            str(tmp_path / "companies.sqlite3"),
            "--output",
            str(tmp_path / "cases.jsonl"),
            "--sample-size",
            "12",
            "--seed",
            "fixed",
        ]
    )
    payload, _ = parse_stdout(capsys)

    assert exit_code == EXIT_SUCCESS
    assert payload == {"selected_count": 12, "source": "corpweb"}
    assert captured["sample_size"] == 12
    assert captured["seed"] == "fixed"


def test_evaluate_run_passes_bounded_execution_options(monkeypatch, capsys, tmp_path: Path) -> None:
    captured: dict = {}

    async def fake_run(dataset, results, **kwargs):
        captured.update({"dataset": dataset, "results": results, **kwargs})
        return {"total_cases": 4, "completed_cases": 4, "self_reported_success_rate": 0.5}

    monkeypatch.setattr("job_page_finder.cli.run_evaluation", fake_run)
    exit_code = main(
        [
            "evaluate",
            "run",
            "--dataset",
            str(tmp_path / "cases.jsonl"),
            "--results",
            str(tmp_path / "results.jsonl"),
            "--workers",
            "3",
            "--case-timeout",
            "90",
            "--run-id",
            "pilot",
        ]
    )
    payload, _ = parse_stdout(capsys)

    assert exit_code == EXIT_SUCCESS
    assert payload["completed_cases"] == 4
    assert captured["workers"] == 3
    assert captured["case_timeout"] == 90
    assert captured["run_id"] == "pilot"


def test_evaluate_run_derives_result_and_diagnostics_paths(monkeypatch, capsys, tmp_path: Path) -> None:
    captured: dict = {}

    async def fake_run(dataset, results, **kwargs):
        captured.update({"dataset": dataset, "results": results, **kwargs})
        return {"total_cases": 0, "completed_cases": 0, "self_reported_success_rate": 0.0}

    monkeypatch.setattr("job_page_finder.cli.run_evaluation", fake_run)
    dataset = tmp_path / "evaluation" / "cases.jsonl"

    assert (
        main(
            [
                "evaluate",
                "run",
                "--dataset",
                str(dataset),
                "--run-id",
                "pilot",
            ]
        )
        == EXIT_SUCCESS
    )
    parse_stdout(capsys)

    assert captured["results"] == tmp_path / "evaluation" / "runs" / "pilot" / "results.jsonl"
    assert captured["config"].diagnostics_level == "diagnostic"
    assert captured["config"].diagnostics_root == (
        tmp_path / "evaluation" / "runs" / "pilot" / "diagnostics-diagnostic"
    )


def test_evaluate_run_creates_default_sidecars(monkeypatch, capsys, tmp_path: Path) -> None:
    captured: dict = {}
    dataset = tmp_path / "evaluation" / "cases.jsonl"
    dataset.parent.mkdir()
    case = EvaluationCase(
        case_id="corpweb:SSE:600001",
        company_name="Company",
        company_url="https://example.com/",
        source="corpweb",
        sample_bucket="SSE:MAIN",
    )
    dataset.write_text(case.model_dump_json() + "\n", encoding="utf-8")

    class Application:
        async def run(self, request):
            return TaskResult(
                version="v1",
                task_id=request["task_id"],
                type="find_job_page",
                status="succeeded",
                output=FindJobPageOutput(
                    job_page_url="https://example.com/careers",
                    job_title="Engineer",
                    evidence="Engineer",
                    steps=1,
                ),
                error=None,
                metadata=TaskMetadata(duration_ms=1),
            )

    def fake_build(settings, **kwargs):
        captured["config"] = RuntimeConfig(
            diagnostics_level=settings.diagnostics.level,
            diagnostics_root=settings.diagnostics.root,
        )
        return Application()

    monkeypatch.setattr("job_page_finder.evaluation._impl.build_application", fake_build)
    assert (
        main(
            [
                "evaluate",
                "run",
                "--dataset",
                str(dataset),
                "--run-id",
                "baseline",
            ]
        )
        == EXIT_SUCCESS
    )
    parse_stdout(capsys)

    run_root = dataset.parent / "runs" / "baseline"
    assert (run_root / "results.jsonl").is_file()
    assert (run_root / "results.manifest.json").is_file()
    assert (run_root / "results.lock.json").is_file()
    assert captured["config"].diagnostics_level == "diagnostic"
    assert captured["config"].diagnostics_root == run_root / "diagnostics-diagnostic"


@pytest.mark.parametrize("run_id", ["../escape", "nested/run", "/tmp/absolute", ".."])
def test_evaluate_run_rejects_unsafe_run_id(monkeypatch, capsys, tmp_path: Path, run_id: str) -> None:
    called = False

    async def fake_run(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr("job_page_finder.cli.run_evaluation", fake_run)
    assert (
        main(
            [
                "evaluate",
                "run",
                "--dataset",
                str(tmp_path / "cases.jsonl"),
                "--run-id",
                run_id,
            ]
        )
        == EXIT_INVALID_INPUT
    )
    payload, _ = parse_stdout(capsys)

    assert payload["error"]["code"] == "INVALID_EVALUATION"
    assert not called


def test_evaluate_reports_invalid_input_as_json(monkeypatch, capsys, tmp_path: Path) -> None:
    from job_page_finder.evaluation import EvaluationError

    def fail(*args, **kwargs):
        raise EvaluationError("database missing")

    monkeypatch.setattr("job_page_finder.cli.generate_corpweb_dataset", fail)
    assert (
        main(
            [
                "evaluate",
                "generate",
                "--db",
                str(tmp_path / "missing.sqlite3"),
                "--output",
                str(tmp_path / "cases.jsonl"),
            ]
        )
        == EXIT_INVALID_INPUT
    )
    payload, _ = parse_stdout(capsys)
    assert payload["error"] == {"code": "INVALID_EVALUATION", "message": "database missing"}
