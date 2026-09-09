import asyncio
import errno
import json
import sqlite3
from pathlib import Path

import pytest

import job_page_finder.evaluation as evaluation_module
from job_page_finder.evaluation import (
    EvaluationCase,
    EvaluationError,
    _evaluation_lock,
    dataset_fingerprint,
    generate_corpweb_dataset,
    read_cases,
    read_run_records,
    run_evaluation,
    summarize,
    write_summary,
)
from job_page_finder.runner import FindJobPageOutput, TaskMetadata, TaskResult
from job_page_finder.runtime import RuntimeConfig


def create_corpweb_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE companies (
            id INTEGER PRIMARY KEY,
            exchange TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            stock_name TEXT NOT NULL,
            company_name TEXT,
            board TEXT NOT NULL,
            industry TEXT
        );
        CREATE TABLE websites (
            company_id INTEGER NOT NULL,
            url_raw TEXT,
            url_normalized TEXT,
            status TEXT NOT NULL,
            http_status INTEGER,
            final_url TEXT,
            checked_at TEXT
        );
        """
    )
    rows = [
        (1, "SSE", "600001", "上证一", "上证一公司", "MAIN", "制造业", "one.cn", "https://one.cn/", "VALID", 200),
        (2, "SSE", "600002", "上证二", "上证二公司", "MAIN", "软件业", "two.cn", "https://two.cn/", "VALID", 200),
        (3, "SSE", "688001", "科创一", "科创一公司", "STAR", "软件业", "star.cn", "https://star.cn/", "VALID", 200),
        (4, "SZSE", "000001", "深证一", "深证一公司", "MAIN", "金融业", "main.cn", "https://main.cn/", "VALID", 200),
        (
            5,
            "SZSE",
            "300001",
            "创业一",
            "创业一公司",
            "CHINEXT",
            "制造业",
            "gem.cn",
            "https://gem.cn/",
            "VALID",
            202,
        ),
        (
            6,
            "SZSE",
            "300002",
            "受限",
            "受限公司",
            "CHINEXT",
            "制造业",
            "blocked.cn",
            "https://blocked.cn/",
            "RESTRICTED",
            403,
        ),
        (
            7,
            "SZSE",
            "300003",
            "不可达",
            "不可达公司",
            "CHINEXT",
            "制造业",
            "down.cn",
            "https://down.cn/",
            "UNREACHABLE",
            502,
        ),
        (8, "SSE", "600008", "坏地址", "坏地址公司", "MAIN", "制造业", "bad", "not-a-url", "VALID", 200),
    ]
    connection.executemany(
        "INSERT INTO companies VALUES (?, ?, ?, ?, ?, ?, ?)",
        [row[:7] for row in rows],
    )
    connection.executemany(
        "INSERT INTO websites VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (
                row[0],
                row[7],
                row[8],
                row[9],
                row[10],
                f"http://{row[7]}/" if row[0] in {1, 2} else row[8],
                "2026-09-08T00:00:00Z",
            )
            for row in rows
        ],
    )
    connection.commit()
    connection.close()


def test_generate_samples_only_valid_sites_with_generic_required_fields(tmp_path: Path) -> None:
    database = tmp_path / "companies.sqlite3"
    dataset = tmp_path / "cases.jsonl"
    create_corpweb_database(database)

    manifest = generate_corpweb_dataset(database, dataset, sample_size=4, seed="fixed")
    cases = read_cases(dataset)

    assert len(cases) == 4
    assert manifest["eligible_count"] == 5
    assert manifest["rejected_count"] == 1
    assert {case.sample_bucket for case in cases} == {"SSE:MAIN", "SSE:STAR", "SZSE:MAIN", "SZSE:CHINEXT"}
    assert all(case.source == "corpweb" for case in cases)
    assert all(case.case_id.startswith("corpweb:") for case in cases)
    assert all("metadata" not in case.model_dump(mode="json") for case in cases)
    assert all(str(case.company_url).startswith("http://") for case in cases if case.sample_bucket == "SSE:MAIN")
    assert not any(case.case_id.endswith(("300002", "300003")) for case in cases)
    assert (tmp_path / "cases.manifest.json").is_file()


def test_generate_is_deterministic_and_rejects_oversized_sample(tmp_path: Path) -> None:
    database = tmp_path / "companies.sqlite3"
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    create_corpweb_database(database)

    generate_corpweb_dataset(database, first, sample_size=3, seed="same")
    generate_corpweb_dataset(database, second, sample_size=3, seed="same")

    first_cases = read_cases(first)
    second_cases = read_cases(second)
    assert [case.case_id for case in first_cases] == [case.case_id for case in second_cases]
    assert dataset_fingerprint(first_cases) == dataset_fingerprint(second_cases)
    with pytest.raises(EvaluationError, match="exceeds eligible population"):
        generate_corpweb_dataset(database, tmp_path / "too-many.jsonl", sample_size=6)


def test_dataset_rejects_non_corpweb_source(tmp_path: Path) -> None:
    dataset = tmp_path / "cases.jsonl"
    case = EvaluationCase(
        case_id="corpweb:SSE:600001",
        company_name="Company",
        company_url="https://example.com/",
        source="corpweb",
        sample_bucket="SSE:MAIN",
    ).model_dump(mode="json")
    case["source"] = "other"
    dataset.write_text(json.dumps(case) + "\n", encoding="utf-8")

    with pytest.raises(EvaluationError, match="source"):
        read_cases(dataset)


def write_cases(path: Path, count: int = 4) -> list[EvaluationCase]:
    cases = [
        EvaluationCase(
            case_id=f"case-{index}",
            company_name=f"Company {index}",
            company_url=f"https://company-{index}.example/",
            source="corpweb",
            sample_bucket="bucket-a" if index % 2 else "bucket-b",
        )
        for index in range(count)
    ]
    path.write_text(
        "".join(json.dumps(case.model_dump(mode="json"), ensure_ascii=False) + "\n" for case in cases),
        encoding="utf-8",
    )
    return cases


@pytest.mark.asyncio
async def test_run_evaluation_bounds_concurrency_and_resumes(tmp_path: Path) -> None:
    dataset = tmp_path / "cases.jsonl"
    results = tmp_path / "results.jsonl"
    write_cases(dataset)
    active = 0
    maximum_active = 0
    calls: list[str] = []

    async def fake_run(request, config):
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        calls.append(request["task_id"])
        await asyncio.sleep(0.01)
        active -= 1
        return TaskResult(
            version="v1",
            task_id=request["task_id"],
            type="find_job_page",
            status="succeeded",
            output=FindJobPageOutput(
                job_page_url="https://jobs.example/",
                job_title="Engineer",
                evidence="Engineer",
                steps=2,
            ),
            error=None,
            metadata=TaskMetadata(duration_ms=10),
        )

    summary = await run_evaluation(
        dataset,
        results,
        workers=2,
        case_timeout=1,
        config=RuntimeConfig(diagnostics_root=tmp_path / "diagnostics"),
        run_task_fn=fake_run,
    )

    assert maximum_active == 2
    assert summary["completed_cases"] == 4
    assert summary["finder_succeeded"] == 4
    assert summary["self_reported_success_rate"] == 1.0
    assert len(read_run_records(results)) == 4

    await run_evaluation(
        dataset,
        results,
        workers=2,
        case_timeout=1,
        config=RuntimeConfig(diagnostics_root=tmp_path / "diagnostics"),
        run_task_fn=fake_run,
    )
    assert len(calls) == 4


@pytest.mark.asyncio
async def test_run_records_timeout_and_finder_failure_separately(tmp_path: Path) -> None:
    dataset = tmp_path / "cases.jsonl"
    results = tmp_path / "results.jsonl"
    write_cases(dataset, count=2)

    async def fake_run(request, config):
        if request["task_id"] == "case-0":
            await asyncio.sleep(0.05)
        return TaskResult(
            version="v1",
            task_id=request["task_id"],
            type="find_job_page",
            status="failed",
            output=None,
            error={"code": "MAX_STEPS_REACHED", "message": "not found", "retryable": False},
            metadata=TaskMetadata(duration_ms=5),
        )

    summary = await run_evaluation(
        dataset,
        results,
        workers=2,
        case_timeout=0.01,
        config=RuntimeConfig(diagnostics_root=tmp_path / "diagnostics"),
        run_task_fn=fake_run,
    )

    assert summary["execution_statuses"] == {"COMPLETED": 1, "TIMEOUT": 1}
    assert summary["finder_failed"] == 1
    assert summary["error_codes"] == {"MAX_STEPS_REACHED": 1, "TIMEOUT": 1}
    assert summary["by_sample_bucket"]["bucket-b"]["finder_failed"] == 0
    assert [value["error_code"] for value in summary["failed_cases"]] == ["TIMEOUT", "MAX_STEPS_REACHED"]


@pytest.mark.asyncio
async def test_executor_cancelled_error_becomes_runner_failure(tmp_path: Path) -> None:
    dataset = tmp_path / "cases.jsonl"
    results = tmp_path / "results.jsonl"
    write_cases(dataset, count=1)

    async def cancelled(request, config):
        raise asyncio.CancelledError

    summary = await asyncio.wait_for(
        run_evaluation(
            dataset,
            results,
            workers=1,
            case_timeout=1,
            config=RuntimeConfig(diagnostics_root=tmp_path / "diagnostics"),
            run_task_fn=cancelled,
        ),
        timeout=1,
    )

    assert summary["execution_statuses"] == {"RUNNER_ERROR": 1}
    assert summary["error_codes"] == {"RUNNER_ERROR": 1}


@pytest.mark.asyncio
async def test_resume_requires_manifest_and_repairs_torn_final_line(tmp_path: Path) -> None:
    dataset = tmp_path / "cases.jsonl"
    results = tmp_path / "results.jsonl"
    write_cases(dataset, count=1)

    async def fake_run(request, config):
        return TaskResult(
            version="v1",
            task_id=request["task_id"],
            type="find_job_page",
            status="succeeded",
            output=FindJobPageOutput(
                job_page_url="https://jobs.example/",
                job_title="Engineer",
                evidence="Engineer",
                steps=1,
            ),
            error=None,
            metadata=TaskMetadata(duration_ms=1),
        )

    config = RuntimeConfig(diagnostics_root=tmp_path / "diagnostics")
    await run_evaluation(dataset, results, workers=1, case_timeout=1, config=config, run_task_fn=fake_run)
    with results.open("ab") as stream:
        stream.write(b'{"partial"')

    summary = await run_evaluation(dataset, results, workers=1, case_timeout=1, config=config, run_task_fn=fake_run)
    assert summary["completed_cases"] == 1
    assert results.read_bytes().endswith(b"\n")

    (tmp_path / "results.manifest.json").unlink()
    with pytest.raises(EvaluationError, match="missing its run manifest"):
        await run_evaluation(dataset, results, workers=1, case_timeout=1, config=config, run_task_fn=fake_run)


@pytest.mark.asyncio
@pytest.mark.parametrize("run_id", ["../escape", "nested/run", "/tmp/absolute", ".."])
async def test_run_evaluation_rejects_unsafe_run_id(tmp_path: Path, run_id: str) -> None:
    dataset = tmp_path / "cases.jsonl"
    results = tmp_path / "results.jsonl"
    write_cases(dataset, count=1)

    with pytest.raises(EvaluationError, match="safe path component"):
        await run_evaluation(dataset, results, run_id=run_id)

    assert not results.exists()
    assert not results.with_name("results.manifest.json").exists()
    assert not results.with_name("results.lock.json").exists()


@pytest.mark.asyncio
async def test_results_path_expands_user_before_manifest_and_lock_access(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    dataset = tmp_path / "cases.jsonl"
    results = Path("~") / "jobfinder-evaluation" / "results.jsonl"
    write_cases(dataset, count=1)

    calls = 0

    async def fake_run(request, config):
        nonlocal calls
        calls += 1
        return TaskResult(
            version="v1",
            task_id=request["task_id"],
            type="find_job_page",
            status="succeeded",
            output=FindJobPageOutput(
                job_page_url="https://jobs.example/",
                job_title="Engineer",
                evidence="Engineer",
                steps=1,
            ),
            error=None,
            metadata=TaskMetadata(duration_ms=1),
        )

    first_summary = await run_evaluation(
        dataset,
        results,
        workers=1,
        case_timeout=1,
        config=RuntimeConfig(diagnostics_root=tmp_path / "diagnostics"),
        run_task_fn=fake_run,
    )

    expanded_results = results.expanduser()
    assert first_summary["completed_cases"] == 1
    assert calls == 1
    assert expanded_results.is_file()
    assert expanded_results.with_name("results.manifest.json").is_file()
    assert expanded_results.with_name("results.lock.json").is_file()

    expanded_results.write_bytes(expanded_results.read_bytes().rstrip(b"\n"))
    no_newline_summary = await run_evaluation(
        dataset,
        results,
        workers=1,
        case_timeout=1,
        config=RuntimeConfig(diagnostics_root=tmp_path / "diagnostics"),
        run_task_fn=fake_run,
    )
    assert no_newline_summary["completed_cases"] == 1
    assert calls == 1
    assert expanded_results.read_bytes().endswith(b"\n")

    with expanded_results.open("ab") as stream:
        stream.write(b'{"partial"')

    second_summary = await run_evaluation(
        dataset,
        results,
        workers=1,
        case_timeout=1,
        config=RuntimeConfig(diagnostics_root=tmp_path / "diagnostics"),
        run_task_fn=fake_run,
    )
    assert second_summary["completed_cases"] == 1
    assert calls == 1
    assert len(read_run_records(expanded_results)) == 1
    assert expanded_results.read_bytes().endswith(b"\n")


@pytest.mark.asyncio
async def test_missing_manifest_is_checked_before_torn_line_repair(tmp_path: Path) -> None:
    dataset = tmp_path / "cases.jsonl"
    results = tmp_path / "results.jsonl"
    write_cases(dataset, count=1)
    results.write_bytes(b'{"partial"')

    with pytest.raises(EvaluationError, match="missing its run manifest"):
        await run_evaluation(dataset, results, workers=1, case_timeout=1)

    assert results.read_bytes() == b'{"partial"'


@pytest.mark.asyncio
async def test_concurrent_evaluations_share_results_lock_without_deadlock(tmp_path: Path) -> None:
    dataset = tmp_path / "cases.jsonl"
    results = tmp_path / "results.jsonl"
    write_cases(dataset, count=1)
    calls = 0

    async def fake_run(request, config):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        return TaskResult(
            version="v1",
            task_id=request["task_id"],
            type="find_job_page",
            status="succeeded",
            output=FindJobPageOutput(
                job_page_url="https://jobs.example/",
                job_title="Engineer",
                evidence="Engineer",
                steps=1,
            ),
            error=None,
            metadata=TaskMetadata(duration_ms=1),
        )

    config = RuntimeConfig(diagnostics_root=tmp_path / "diagnostics")
    summaries = await asyncio.wait_for(
        asyncio.gather(
            run_evaluation(dataset, results, workers=1, case_timeout=1, config=config, run_task_fn=fake_run),
            run_evaluation(dataset, results, workers=1, case_timeout=1, config=config, run_task_fn=fake_run),
        ),
        timeout=2,
    )

    assert [summary["completed_cases"] for summary in summaries] == [1, 1]
    assert calls == 1
    assert len(read_run_records(results)) == 1


@pytest.mark.asyncio
async def test_lock_body_error_is_not_mislabeled_and_lock_remains_usable(tmp_path: Path) -> None:
    results = tmp_path / "results.jsonl"

    with pytest.raises(OSError, match="body failure"):
        async with _evaluation_lock(results):
            raise OSError("body failure")

    async with asyncio.timeout(1):
        async with _evaluation_lock(results):
            pass


@pytest.mark.asyncio
async def test_cancelling_lock_holder_releases_lock(tmp_path: Path) -> None:
    results = tmp_path / "results.jsonl"
    entered = asyncio.Event()

    async def hold_lock() -> None:
        async with _evaluation_lock(results):
            entered.set()
            await asyncio.sleep(10)

    holder = asyncio.create_task(hold_lock())
    await entered.wait()
    holder.cancel()
    with pytest.raises(asyncio.CancelledError):
        await holder

    async with asyncio.timeout(1):
        async with _evaluation_lock(results):
            pass


@pytest.mark.asyncio
async def test_lock_setup_errors_are_evaluation_errors(monkeypatch, tmp_path: Path) -> None:
    results = tmp_path / "results.jsonl"

    def fail_mkdir(self, *args, **kwargs):
        raise PermissionError("mkdir failure")

    monkeypatch.setattr(Path, "mkdir", fail_mkdir)
    with pytest.raises(EvaluationError, match="could not lock evaluation results"):
        async with _evaluation_lock(results):
            pass


@pytest.mark.asyncio
async def test_lock_open_and_acquire_errors_are_evaluation_errors(monkeypatch, tmp_path: Path) -> None:
    results = tmp_path / "results.jsonl"

    def fail_open(self, *args, **kwargs):
        raise PermissionError("open failure")

    monkeypatch.setattr(Path, "open", fail_open)
    with pytest.raises(EvaluationError, match="could not lock evaluation results"):
        async with _evaluation_lock(results):
            pass

    monkeypatch.undo()

    def fail_flock(*args, **kwargs):
        raise OSError("flock failure")

    monkeypatch.setattr(evaluation_module.fcntl, "flock", fail_flock)
    with pytest.raises(EvaluationError, match="could not lock evaluation results"):
        async with _evaluation_lock(results):
            pass

    attempts = 0

    def contend_once(fd, operation):
        nonlocal attempts
        if operation & evaluation_module.fcntl.LOCK_EX and attempts == 0:
            attempts += 1
            raise OSError(errno.EACCES, "lock is busy")

    monkeypatch.setattr(evaluation_module.fcntl, "flock", contend_once)
    async with _evaluation_lock(results):
        pass
    assert attempts == 1


@pytest.mark.asyncio
async def test_lock_close_error_is_wrapped_without_masking_body_error(monkeypatch, tmp_path: Path) -> None:
    results = tmp_path / "results.jsonl"

    class FakeLockFile:
        def fileno(self):
            return 1

        def close(self):
            raise OSError("close failure")

    monkeypatch.setattr(Path, "open", lambda self, *args, **kwargs: FakeLockFile())
    monkeypatch.setattr(evaluation_module.fcntl, "flock", lambda *args, **kwargs: None)

    with pytest.raises(EvaluationError, match="could not close evaluation lock"):
        async with _evaluation_lock(results):
            pass

    def fail_unlock(fd, operation):
        if operation == evaluation_module.fcntl.LOCK_UN:
            raise OSError("unlock failure")

    monkeypatch.setattr(evaluation_module.fcntl, "flock", fail_unlock)
    with pytest.raises(EvaluationError, match="could not unlock evaluation results"):
        async with _evaluation_lock(results):
            pass

    with pytest.raises(OSError, match="body failure"):
        async with _evaluation_lock(results):
            raise OSError("body failure")


@pytest.mark.asyncio
async def test_cancelling_lock_waiter_closes_waiter_and_preserves_holder(monkeypatch, tmp_path: Path) -> None:
    results = tmp_path / "results.jsonl"
    entered = asyncio.Event()
    waiter_opened = asyncio.Event()
    opened_files = []

    class TrackedLockFile:
        def __init__(self, file):
            self.file = file
            self.closed = False

        def fileno(self):
            return self.file.fileno()

        def close(self):
            self.closed = True
            return self.file.close()

    real_open = Path.open

    def tracked_open(path, *args, **kwargs):
        lock_file = TrackedLockFile(real_open(path, *args, **kwargs))
        opened_files.append(lock_file)
        if len(opened_files) == 2:
            waiter_opened.set()
        return lock_file

    monkeypatch.setattr(Path, "open", tracked_open)

    async def hold_lock() -> None:
        async with _evaluation_lock(results):
            entered.set()
            await asyncio.sleep(10)

    async def wait_for_lock() -> None:
        async with _evaluation_lock(results):
            pass

    holder = asyncio.create_task(hold_lock())
    await entered.wait()
    waiter = asyncio.create_task(wait_for_lock())
    await waiter_opened.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert opened_files[1].closed

    holder.cancel()
    with pytest.raises(asyncio.CancelledError):
        await holder
    assert opened_files[0].closed

    async with asyncio.timeout(1):
        async with _evaluation_lock(results):
            pass


def test_summary_writes_machine_and_human_readable_reports(tmp_path: Path) -> None:
    dataset = tmp_path / "cases.jsonl"
    results = tmp_path / "results.jsonl"
    json_output = tmp_path / "summary.json"
    markdown_output = tmp_path / "summary.md"
    write_cases(dataset, count=1)

    summary = summarize(dataset, results)
    write_summary(summary, json_output, markdown_output)

    assert json.loads(json_output.read_text(encoding="utf-8"))["pending_cases"] == 1
    markdown = markdown_output.read_text(encoding="utf-8")
    assert "JobFinder Operational Evaluation" in markdown
    assert "does not measure accuracy or recall" in markdown
