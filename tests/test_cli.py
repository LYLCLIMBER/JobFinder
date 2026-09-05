import json
from pathlib import Path

from job_page_finder.cli import (
    EXIT_CONFIGURATION_ERROR,
    EXIT_INVALID_INPUT,
    EXIT_SUCCESS,
    EXIT_TASK_FAILED,
    main,
)
from job_page_finder.models import JobPageFinderInput, JobPageFinderResult
from job_page_finder.runner import TaskRunner


def install_fake_finder(monkeypatch, result: JobPageFinderResult | Exception):
    async def fake_find(finder_input: JobPageFinderInput) -> JobPageFinderResult:
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(
        "job_page_finder.runtime.create_runner",
        lambda **kwargs: TaskRunner(fake_find),
    )


def parse_stdout(capsys) -> tuple[dict, str]:
    captured = capsys.readouterr()
    return json.loads(captured.out), captured.err


def test_find_job_page_shortcut_prints_json_and_exits_zero(monkeypatch, capsys) -> None:
    """Accept find-job-page arguments and print a succeeded JSON result."""
    install_fake_finder(
        monkeypatch,
        JobPageFinderResult(
            success=True,
            job_page_url="https://example.com/careers",
            job_title="Senior Backend Engineer",
            evidence="Senior Backend Engineer",
            steps=3,
        ),
    )

    exit_code = main(["find-job-page", "https://example.com", "--max-steps", "4"])
    payload, stderr = parse_stdout(capsys)

    assert exit_code == EXIT_SUCCESS
    assert payload["status"] == "succeeded"
    assert payload["type"] == "find_job_page"
    assert payload["output"]["job_title"] == "Senior Backend Engineer"
    assert payload["error"] is None
    assert isinstance(payload["metadata"]["duration_ms"], int)
    assert not stderr or "task_id=" in stderr


def test_run_json_file_uses_the_same_result_contract(monkeypatch, capsys, tmp_path: Path) -> None:
    """Read a JSON task file and print the same unified result envelope."""
    install_fake_finder(
        monkeypatch,
        JobPageFinderResult(
            success=True,
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
        JobPageFinderResult(success=True, job_page_url="x", job_title="x", evidence="x", steps=1),
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


def test_task_failure_uses_exit_code_one(monkeypatch, capsys) -> None:
    """Use a non-zero task failure exit code when the finder returns a domain error."""
    install_fake_finder(
        monkeypatch,
        JobPageFinderResult(
            success=False,
            steps=8,
            error="Maximum steps reached without finding a specific job",
            error_code="MAX_STEPS_REACHED",
        ),
    )

    exit_code = main(["find-job-page", "https://example.com"])
    payload, _ = parse_stdout(capsys)

    assert exit_code == EXIT_TASK_FAILED
    assert payload["status"] == "failed"
    assert payload["error"]["code"] == "MAX_STEPS_REACHED"
    assert payload["output"] is None


def test_configuration_error_uses_exit_code_three(monkeypatch, capsys) -> None:
    """Surface composition failures as CONFIGURATION_ERROR with a dedicated exit code."""

    def boom(**kwargs):
        raise RuntimeError("DEEPSEEK_API_KEY is not configured")

    monkeypatch.setattr("job_page_finder.runtime.create_runner", boom)
    exit_code = main(["find-job-page", "https://example.com"])
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
    original = Path.read_text

    def fake_read_text(self, *args, **kwargs):
        if self == blocked:
            raise PermissionError("denied")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fake_read_text)
    exit_code = main(["run", str(blocked)])
    payload, stderr = parse_stdout(capsys)

    assert exit_code == EXIT_INVALID_INPUT
    assert payload["error"]["code"] == "INVALID_TASK"
    assert "Traceback" not in json.dumps(payload)
    assert "Traceback" not in stderr


def test_invalid_shortcut_is_not_masked_by_configuration_error(monkeypatch, capsys) -> None:
    """Reject illegal CLI payload before composition so the exit code stays 2."""

    def boom(**kwargs):
        raise RuntimeError("DEEPSEEK_API_KEY is not configured")

    monkeypatch.setattr("job_page_finder.runtime.create_runner", boom)
    exit_code = main(["find-job-page", "not-a-url"])
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
