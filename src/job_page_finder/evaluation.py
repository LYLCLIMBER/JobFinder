from __future__ import annotations

import asyncio
import errno
import fcntl
import hashlib
import json
import math
import os
import re
import sqlite3
import time
from collections import Counter, defaultdict, deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, ValidationError, field_validator, model_validator

from job_page_finder import runtime
from job_page_finder.runner import TaskResult
from job_page_finder.runtime import RuntimeConfig

SCHEMA_VERSION = "v1"
DEFAULT_SAMPLE_SIZE = 120
DEFAULT_SAMPLE_SEED = "jobfinder-pilot-v1"
_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class EvaluationError(ValueError):
    pass


def validate_evaluation_run_id(run_id: str) -> str:
    if not run_id.strip():
        raise EvaluationError("run_id must not be blank")
    if _RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise EvaluationError("run_id must be a single safe path component using letters, digits, '.', '_' or '-'")
    return run_id


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v1"] = SCHEMA_VERSION
    case_id: str = Field(min_length=1)
    company_name: str = Field(min_length=1)
    company_url: HttpUrl
    source: Literal["corpweb"]
    sample_bucket: str = Field(min_length=1)
    max_steps: int = Field(default=8, ge=1, le=50)

    @field_validator("case_id", "company_name", "source", "sample_bucket")
    @classmethod
    def strip_non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    def task_request(self) -> dict[str, Any]:
        return {
            "version": "v1",
            "task_id": self.case_id,
            "type": "find_job_page",
            "payload": {
                "company_url": str(self.company_url),
                "max_steps": self.max_steps,
            },
        }


class EvaluationRunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v1"] = SCHEMA_VERSION
    run_id: str = Field(min_length=1)
    dataset_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    case_id: str = Field(min_length=1)
    attempt: int = Field(default=1, ge=1)
    execution_status: Literal["COMPLETED", "TIMEOUT", "RUNNER_ERROR"]
    started_at: datetime
    finished_at: datetime
    duration_ms: int = Field(ge=0)
    finder_result: TaskResult | None
    runner_error: str | None

    @model_validator(mode="after")
    def validate_payload(self) -> "EvaluationRunRecord":
        if self.execution_status == "COMPLETED":
            if self.finder_result is None or self.runner_error is not None:
                raise ValueError("completed records require finder_result and no runner_error")
        elif self.finder_result is not None or not self.runner_error:
            raise ValueError("non-completed records require runner_error and no finder_result")
        return self


RunTask = Callable[[Mapping[str, Any], RuntimeConfig], Awaitable[TaskResult]]


@dataclass(frozen=True)
class _CorpWebCandidate:
    case: EvaluationCase
    industry: str


def generate_corpweb_dataset(
    db_path: Path,
    output_path: Path,
    *,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
    seed: str = DEFAULT_SAMPLE_SEED,
    max_steps: int = 8,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    if sample_size <= 0:
        raise EvaluationError("sample_size must be positive")
    if not seed.strip():
        raise EvaluationError("seed must not be blank")
    if not 1 <= max_steps <= 50:
        raise EvaluationError("max_steps must be between 1 and 50")
    db_path = db_path.expanduser().resolve()
    if not db_path.is_file():
        raise EvaluationError(f"CorpWeb database does not exist: {db_path}")

    population, rejected = _load_corpweb_population(db_path, max_steps=max_steps)
    if sample_size > len(population):
        raise EvaluationError(f"sample_size {sample_size} exceeds eligible population {len(population)}")
    selected = _stratified_sample(population, sample_size=sample_size, seed=seed)
    selected.sort(key=lambda case: case.case_id)
    fingerprint = dataset_fingerprint(selected)

    _write_jsonl_atomic(output_path, [case.model_dump(mode="json") for case in selected])
    population_strata = Counter(candidate.case.sample_bucket for candidate in population)
    selected_strata = Counter(case.sample_bucket for case in selected)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "dataset_version": "corpweb-pilot-v1",
        "purpose": "operational_evaluation",
        "source": "corpweb",
        "created_at": _utc_now_text(),
        "database_path": str(db_path),
        "database_sha256": _file_sha256(db_path),
        "dataset_fingerprint": fingerprint,
        "sample_seed": seed,
        "requested_count": sample_size,
        "selected_count": len(selected),
        "eligible_count": len(population),
        "rejected_count": rejected,
        "eligibility": "websites.status = VALID and final_url is a valid HTTP(S) URL",
        "strata": ["exchange", "board"],
        "population_by_bucket": dict(sorted(population_strata.items())),
        "selected_by_bucket": dict(sorted(selected_strata.items())),
        "max_steps": max_steps,
    }
    _write_json_atomic(manifest_path or _sidecar_path(output_path, "manifest"), manifest)
    return manifest


def read_cases(path: Path) -> list[EvaluationCase]:
    records = _read_jsonl(path, "evaluation dataset")
    cases: list[EvaluationCase] = []
    seen: set[str] = set()
    for line_number, record in records:
        try:
            case = EvaluationCase.model_validate(record)
        except ValidationError as exc:
            raise EvaluationError(f"invalid evaluation case at line {line_number}: {_validation_message(exc)}") from exc
        if case.case_id in seen:
            raise EvaluationError(f"duplicate case_id at line {line_number}: {case.case_id}")
        seen.add(case.case_id)
        cases.append(case)
    if not cases:
        raise EvaluationError("evaluation dataset is empty")
    return cases


def dataset_fingerprint(cases: Sequence[EvaluationCase]) -> str:
    digest = hashlib.sha256()
    for case in sorted(cases, key=lambda item: item.case_id):
        encoded = json.dumps(
            case.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        digest.update(encoded)
        digest.update(b"\n")
    return digest.hexdigest()


async def run_evaluation(
    dataset_path: Path,
    results_path: Path,
    *,
    run_id: str = "baseline",
    workers: int = 2,
    case_timeout: float = 300,
    config: RuntimeConfig | None = None,
    run_task_fn: RunTask | None = None,
) -> dict[str, Any]:
    if workers <= 0:
        raise EvaluationError("workers must be positive")
    if not math.isfinite(case_timeout) or case_timeout <= 0:
        raise EvaluationError("case_timeout must be greater than zero")
    validate_evaluation_run_id(run_id)
    dataset_path = dataset_path.expanduser()
    results_path = results_path.expanduser()

    async with _evaluation_lock(results_path):
        return await _run_evaluation_locked(
            dataset_path,
            results_path,
            run_id=run_id,
            workers=workers,
            case_timeout=case_timeout,
            config=config,
            run_task_fn=run_task_fn,
        )


async def _run_evaluation_locked(
    dataset_path: Path,
    results_path: Path,
    *,
    run_id: str,
    workers: int,
    case_timeout: float,
    config: RuntimeConfig | None,
    run_task_fn: RunTask | None,
) -> dict[str, Any]:
    cases = read_cases(dataset_path)
    fingerprint = dataset_fingerprint(cases)
    runtime_config = config or RuntimeConfig()
    _prepare_run_manifest(
        results_path,
        run_id=run_id,
        dataset_fingerprint_value=fingerprint,
        workers=workers,
        case_timeout=case_timeout,
        config=runtime_config,
    )
    _repair_torn_final_line(results_path)
    existing = read_run_records(results_path, expected_fingerprint=fingerprint, expected_run_id=run_id)
    completed_ids = {record.case_id for record in existing}
    pending = [case for case in cases if case.case_id not in completed_ids]
    if not pending:
        return summarize(dataset_path, results_path)

    execute = run_task_fn or _default_run_task
    queue: asyncio.Queue[EvaluationCase | None] = asyncio.Queue()
    result_queue: asyncio.Queue[EvaluationRunRecord] = asyncio.Queue()
    for case in pending:
        queue.put_nowait(case)
    for _ in range(min(workers, len(pending))):
        queue.put_nowait(None)

    async def worker() -> None:
        while True:
            case = await queue.get()
            try:
                if case is None:
                    return
                try:
                    record = await _run_case(
                        case,
                        run_id=run_id,
                        fingerprint=fingerprint,
                        case_timeout=case_timeout,
                        config=runtime_config,
                        execute=execute,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    now = datetime.now(UTC)
                    record = EvaluationRunRecord(
                        run_id=run_id,
                        dataset_fingerprint=fingerprint,
                        case_id=case.case_id,
                        execution_status="RUNNER_ERROR",
                        started_at=now,
                        finished_at=now,
                        duration_ms=0,
                        finder_result=None,
                        runner_error=_clean_error(exc),
                    )
                await result_queue.put(record)
            finally:
                queue.task_done()

    worker_tasks = [asyncio.create_task(worker()) for _ in range(min(workers, len(pending)))]
    try:
        with _open_append(results_path) as stream:
            for _ in pending:
                record = await result_queue.get()
                _append_json_record(stream, record.model_dump(mode="json"))
                result_queue.task_done()
        await queue.join()
        await asyncio.gather(*worker_tasks)
    finally:
        for task in worker_tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*worker_tasks, return_exceptions=True)
    return summarize(dataset_path, results_path)


def read_run_records(
    path: Path,
    *,
    expected_fingerprint: str | None = None,
    expected_run_id: str | None = None,
) -> list[EvaluationRunRecord]:
    if not path.exists():
        return []
    records: list[EvaluationRunRecord] = []
    seen: set[str] = set()
    for line_number, raw in _read_jsonl(path, "evaluation results"):
        try:
            record = EvaluationRunRecord.model_validate(raw)
        except ValidationError as exc:
            raise EvaluationError(f"invalid result at line {line_number}: {_validation_message(exc)}") from exc
        if record.case_id in seen:
            raise EvaluationError(f"duplicate result for case_id at line {line_number}: {record.case_id}")
        if expected_fingerprint is not None and record.dataset_fingerprint != expected_fingerprint:
            raise EvaluationError("results belong to a different evaluation dataset")
        if expected_run_id is not None and record.run_id != expected_run_id:
            raise EvaluationError(f"results belong to run_id {record.run_id}, expected {expected_run_id}")
        seen.add(record.case_id)
        records.append(record)
    return records


def summarize(dataset_path: Path, results_path: Path) -> dict[str, Any]:
    cases = read_cases(dataset_path)
    fingerprint = dataset_fingerprint(cases)
    records = read_run_records(results_path, expected_fingerprint=fingerprint)
    run_ids = {record.run_id for record in records}
    if len(run_ids) > 1:
        raise EvaluationError("results contain more than one run_id")
    case_ids = {case.case_id for case in cases}
    unknown = sorted(record.case_id for record in records if record.case_id not in case_ids)
    if unknown:
        raise EvaluationError(f"results contain case IDs outside the dataset: {', '.join(unknown[:3])}")

    succeeded = [record for record in records if record.finder_result and record.finder_result.status == "succeeded"]
    failed = [record for record in records if record.finder_result and record.finder_result.status == "failed"]
    finder_errors = Counter(
        record.finder_result.error.code
        for record in failed
        if record.finder_result is not None and record.finder_result.error is not None
    )
    execution_statuses = Counter(record.execution_status for record in records)
    errors = finder_errors.copy()
    errors.update(
        record.execution_status for record in records if record.execution_status in {"TIMEOUT", "RUNNER_ERROR"}
    )
    completed_ids = {record.case_id for record in records}
    pending_ids = sorted(case_ids - completed_ids)
    durations = [record.duration_ms for record in records]
    steps = [
        record.finder_result.output.steps
        for record in succeeded
        if record.finder_result and record.finder_result.output
    ]
    finder_completed = len(succeeded) + len(failed)

    case_by_id = {case.case_id: case for case in cases}
    failed_cases = []
    for record in records:
        if record.finder_result is not None and record.finder_result.status == "succeeded":
            continue
        error_code = record.execution_status
        message = record.runner_error
        if record.finder_result is not None and record.finder_result.error is not None:
            error_code = record.finder_result.error.code
            message = record.finder_result.error.message
        failed_cases.append(
            {
                "case_id": record.case_id,
                "company_name": case_by_id[record.case_id].company_name,
                "sample_bucket": case_by_id[record.case_id].sample_bucket,
                "error_code": error_code,
                "message": message,
                "duration_ms": record.duration_ms,
            }
        )
    failed_cases.sort(key=lambda value: value["case_id"])
    bucket_totals = Counter(case.sample_bucket for case in cases)
    bucket_completed = Counter(case_by_id[record.case_id].sample_bucket for record in records)
    bucket_succeeded = Counter(case_by_id[record.case_id].sample_bucket for record in succeeded)
    bucket_failed = Counter(case_by_id[record.case_id].sample_bucket for record in failed)
    by_bucket: dict[str, dict[str, Any]] = {}
    for bucket in sorted(bucket_totals):
        completed = bucket_completed[bucket]
        success_count = bucket_succeeded[bucket]
        by_bucket[bucket] = {
            "total": bucket_totals[bucket],
            "completed": completed,
            "finder_succeeded": success_count,
            "finder_failed": bucket_failed[bucket],
            "self_reported_success_rate": _rate(success_count, success_count + bucket_failed[bucket]),
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "purpose": "operational_evaluation",
        "generated_at": _utc_now_text(),
        "dataset_fingerprint": fingerprint,
        "run_id": records[0].run_id if records else None,
        "total_cases": len(cases),
        "completed_cases": len(records),
        "pending_cases": len(pending_ids),
        "completion_rate": _rate(len(records), len(cases)),
        "finder_succeeded": len(succeeded),
        "finder_failed": len(failed),
        "self_reported_success_rate": _rate(len(succeeded), finder_completed),
        "execution_statuses": dict(sorted(execution_statuses.items())),
        "error_codes": dict(sorted(errors.items())),
        "duration_ms": _distribution(durations),
        "successful_steps": _distribution(steps),
        "by_sample_bucket": by_bucket,
        "failed_cases": failed_cases,
        "pending_case_ids": pending_ids,
    }


def write_summary(summary: Mapping[str, Any], json_path: Path, markdown_path: Path) -> None:
    _write_json_atomic(json_path, dict(summary))
    lines = [
        "# JobFinder Operational Evaluation",
        "",
        f"- Dataset fingerprint: `{summary['dataset_fingerprint']}`",
        f"- Run ID: `{summary.get('run_id') or 'n/a'}`",
        f"- Cases: {summary['completed_cases']} / {summary['total_cases']} completed",
        f"- JobFinder self-reported success: {summary['finder_succeeded']} / "
        f"{summary['finder_succeeded'] + summary['finder_failed']} "
        f"({_percent(summary['self_reported_success_rate'])})",
        "",
        "## Sample Buckets",
        "",
        "| Bucket | Total | Completed | Succeeded | Failed | Self-reported success |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for bucket, values in summary["by_sample_bucket"].items():
        lines.append(
            f"| {bucket} | {values['total']} | {values['completed']} | {values['finder_succeeded']} | "
            f"{values['finder_failed']} | {_percent(values['self_reported_success_rate'])} |"
        )
    lines.extend(["", "## Errors", "", "| Error code | Count |", "| --- | ---: |"])
    errors = summary["error_codes"]
    if errors:
        lines.extend(f"| {code} | {count} |" for code, count in errors.items())
    else:
        lines.append("| None | 0 |")
    lines.extend(
        [
            "",
            "## Failed Cases",
            "",
            "| Case | Company | Bucket | Error | Duration (ms) |",
            "| --- | --- | --- | --- | ---: |",
        ]
    )
    failed_cases = summary["failed_cases"]
    if failed_cases:
        lines.extend(
            f"| {value['case_id']} | {value['company_name']} | {value['sample_bucket']} | "
            f"{value['error_code']} | {value['duration_ms']} |"
            for value in failed_cases
        )
    else:
        lines.append("| None | - | - | - | 0 |")
    lines.extend(
        [
            "",
            "## Performance",
            "",
            f"- Duration P50: {summary['duration_ms']['p50']} ms",
            f"- Duration P90: {summary['duration_ms']['p90']} ms",
            f"- Successful steps P50: {summary['successful_steps']['p50']}",
            f"- Successful steps P90: {summary['successful_steps']['p90']}",
            "",
            "> Success is self-reported by JobFinder. This unlabeled evaluation does not measure accuracy or recall.",
            "",
        ]
    )
    _write_text_atomic(markdown_path, "\n".join(lines))


def _load_corpweb_population(db_path: Path, *, max_steps: int) -> tuple[list[_CorpWebCandidate], int]:
    connection = sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT c.id, c.exchange, c.stock_code, c.stock_name, c.company_name,
                   c.board, c.industry, w.url_raw, w.url_normalized,
                   w.status, w.http_status, w.final_url, w.checked_at
            FROM companies AS c
            JOIN websites AS w ON w.company_id = c.id
            WHERE w.status = 'VALID'
              AND w.final_url IS NOT NULL
              AND trim(w.final_url) <> ''
            ORDER BY c.exchange, c.stock_code
            """
        ).fetchall()
    except sqlite3.Error as exc:
        raise EvaluationError(f"could not read CorpWeb database: {exc}") from exc
    finally:
        connection.close()

    candidates: list[_CorpWebCandidate] = []
    rejected = 0
    for row in rows:
        name = row["company_name"] or row["stock_name"]
        exchange = row["exchange"]
        board = row["board"]
        try:
            candidates.append(
                _CorpWebCandidate(
                    case=EvaluationCase(
                        case_id=f"corpweb:{exchange}:{row['stock_code']}",
                        company_name=name,
                        company_url=row["final_url"],
                        source="corpweb",
                        sample_bucket=f"{exchange}:{board}",
                        max_steps=max_steps,
                    ),
                    industry=row["industry"] or "UNKNOWN",
                )
            )
        except ValidationError:
            rejected += 1
    return candidates, rejected


def _stratified_sample(cases: Sequence[_CorpWebCandidate], *, sample_size: int, seed: str) -> list[EvaluationCase]:
    strata: dict[str, list[_CorpWebCandidate]] = defaultdict(list)
    for candidate in cases:
        strata[candidate.case.sample_bucket].append(candidate)
    quotas = _proportional_quotas({key: len(value) for key, value in strata.items()}, sample_size)
    selected: list[EvaluationCase] = []
    for stratum in sorted(strata):
        selected.extend(_sample_diverse_industries(strata[stratum], quotas[stratum], seed=seed))
    return selected


def _proportional_quotas(counts: Mapping[str, int], sample_size: int) -> dict[str, int]:
    total = sum(counts.values())
    raw = {key: sample_size * count / total for key, count in counts.items()}
    quotas = {key: min(counts[key], math.floor(value)) for key, value in raw.items()}
    remaining = sample_size - sum(quotas.values())
    order = sorted(counts, key=lambda key: (-(raw[key] - math.floor(raw[key])), key))
    while remaining:
        progressed = False
        for key in order:
            if quotas[key] >= counts[key]:
                continue
            quotas[key] += 1
            remaining -= 1
            progressed = True
            if not remaining:
                break
        if not progressed:
            raise EvaluationError("could not allocate sampling quotas")
    return quotas


def _sample_diverse_industries(cases: Sequence[_CorpWebCandidate], count: int, *, seed: str) -> list[EvaluationCase]:
    industries: dict[str, deque[_CorpWebCandidate]] = defaultdict(deque)
    grouped: dict[str, list[_CorpWebCandidate]] = defaultdict(list)
    for candidate in cases:
        grouped[candidate.industry].append(candidate)
    for industry, values in grouped.items():
        industries[industry].extend(
            sorted(values, key=lambda candidate: (_stable_key(seed, candidate.case.case_id), candidate.case.case_id))
        )
    industry_order = sorted(industries, key=lambda value: (_stable_key(seed, value), value))
    selected: list[EvaluationCase] = []
    while len(selected) < count:
        for industry in industry_order:
            if industries[industry]:
                selected.append(industries[industry].popleft().case)
                if len(selected) == count:
                    break
    return selected


async def _default_run_task(request: Mapping[str, Any], config: RuntimeConfig) -> TaskResult:
    return await runtime.run_task(request, config=config)


async def _run_case(
    case: EvaluationCase,
    *,
    run_id: str,
    fingerprint: str,
    case_timeout: float,
    config: RuntimeConfig,
    execute: RunTask,
) -> EvaluationRunRecord:
    started_at = datetime.now(UTC)
    started = time.perf_counter()
    try:
        result = await asyncio.wait_for(execute(case.task_request(), config), timeout=case_timeout)
    except TimeoutError:
        status: Literal["TIMEOUT", "RUNNER_ERROR"] = "TIMEOUT"
        finder_result = None
        runner_error = f"case timeout after {case_timeout:g} seconds"
    except asyncio.CancelledError:
        current_task = asyncio.current_task()
        if current_task is not None and current_task.cancelling():
            raise
        status = "RUNNER_ERROR"
        finder_result = None
        runner_error = "CancelledError"
    except Exception as exc:
        status = "RUNNER_ERROR"
        finder_result = None
        runner_error = _clean_error(exc)
    else:
        status = "COMPLETED"
        finder_result = result
        runner_error = None
    return EvaluationRunRecord(
        run_id=run_id,
        dataset_fingerprint=fingerprint,
        case_id=case.case_id,
        execution_status=status,
        started_at=started_at,
        finished_at=datetime.now(UTC),
        duration_ms=max(int((time.perf_counter() - started) * 1000), 0),
        finder_result=finder_result,
        runner_error=runner_error,
    )


def _prepare_run_manifest(
    results_path: Path,
    *,
    run_id: str,
    dataset_fingerprint_value: str,
    workers: int,
    case_timeout: float,
    config: RuntimeConfig,
) -> None:
    path = _sidecar_path(results_path, "manifest")
    expected = {
        "schema_version": SCHEMA_VERSION,
        "purpose": "operational_evaluation",
        "run_id": run_id,
        "dataset_fingerprint": dataset_fingerprint_value,
        "workers": workers,
        "case_timeout_seconds": case_timeout,
        "runtime_config": config.model_dump(mode="json"),
    }
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise EvaluationError(f"could not read run manifest: {exc}") from exc
        comparable = {key: existing.get(key) for key in expected}
        if comparable != expected:
            raise EvaluationError("existing run manifest does not match the requested run configuration")
        return
    if results_path.is_file() and results_path.stat().st_size:
        raise EvaluationError("non-empty results file is missing its run manifest")
    _write_json_atomic(path, {**expected, "created_at": _utc_now_text()})


def _read_jsonl(path: Path, label: str) -> list[tuple[int, Any]]:
    path = path.expanduser()
    if not path.is_file():
        raise EvaluationError(f"{label} does not exist: {path}")
    records: list[tuple[int, Any]] = []
    try:
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise EvaluationError(f"invalid JSON in {label} at line {line_number}: {exc.msg}") from exc
                if not isinstance(value, dict):
                    raise EvaluationError(f"{label} line {line_number} must be a JSON object")
                records.append((line_number, value))
    except (OSError, UnicodeDecodeError) as exc:
        raise EvaluationError(f"could not read {label}: {exc}") from exc
    return records


def _write_jsonl_atomic(path: Path, records: Sequence[Mapping[str, Any]]) -> None:
    text = "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records)
    _write_text_atomic(path, text)


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    _write_text_atomic(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _write_text_atomic(path: Path, text: str) -> None:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)
    except OSError as exc:
        raise EvaluationError(f"could not write {path}: {exc}") from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _open_append(path: Path):
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        return path.open("a", encoding="utf-8")
    except OSError as exc:
        raise EvaluationError(f"could not open results file {path}: {exc}") from exc


def _append_json_record(stream, record: Mapping[str, Any]) -> None:
    try:
        stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    except OSError as exc:
        raise EvaluationError(f"could not append evaluation result: {exc}") from exc


@asynccontextmanager
async def _evaluation_lock(results_path: Path):
    path = _sidecar_path(results_path.expanduser(), "lock")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise EvaluationError(f"could not lock evaluation results: {exc}") from exc
    try:
        lock_file = path.open("a", encoding="utf-8")
    except OSError as exc:
        raise EvaluationError(f"could not lock evaluation results: {exc}") from exc

    primary_failed = False
    lock_acquired = False
    try:
        while True:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                lock_acquired = True
                break
            except OSError as exc:
                if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK}:
                    await asyncio.sleep(0.01)
                    continue
                raise EvaluationError(f"could not lock evaluation results: {exc}") from exc
        try:
            yield
        except BaseException:
            raise
    except BaseException:
        primary_failed = True
        raise
    finally:
        cleanup_error: EvaluationError | None = None
        if lock_acquired:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except OSError as exc:
                cleanup_error = EvaluationError(f"could not unlock evaluation results: {exc}")
        try:
            lock_file.close()
        except OSError as exc:
            if cleanup_error is None:
                cleanup_error = EvaluationError(f"could not close evaluation lock: {exc}")
        if not primary_failed and cleanup_error is not None:
            raise cleanup_error


def _repair_torn_final_line(path: Path) -> None:
    path = path.expanduser()
    if not path.is_file() or path.stat().st_size == 0:
        return
    try:
        with path.open("rb+") as stream:
            stream.seek(-1, os.SEEK_END)
            if stream.read(1) == b"\n":
                return
            stream.seek(0)
            content = stream.read()
            last_newline = content.rfind(b"\n")
            final_fragment = content[last_newline + 1 :]
            try:
                json.loads(final_fragment)
            except (UnicodeDecodeError, json.JSONDecodeError):
                stream.seek(0)
                stream.truncate(last_newline + 1)
            else:
                stream.seek(0, os.SEEK_END)
                stream.write(b"\n")
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise EvaluationError(f"could not repair incomplete evaluation result: {exc}") from exc


def _sidecar_path(path: Path, label: str) -> Path:
    return path.with_name(f"{path.stem}.{label}.json")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise EvaluationError(f"could not hash database: {exc}") from exc
    return digest.hexdigest()


def _stable_key(seed: str, value: str) -> str:
    return hashlib.sha256(f"{seed}:{value}".encode()).hexdigest()


def _validation_message(exc: ValidationError) -> str:
    error = exc.errors()[0]
    location = ".".join(str(part) for part in error.get("loc", ()))
    message = error.get("msg", "invalid")
    return f"{location}: {message}" if location else message


def _clean_error(exc: BaseException) -> str:
    message = " ".join(str(exc).replace("\r", " ").replace("\n", " ").split())
    return (f"{type(exc).__name__}: {message}" if message else type(exc).__name__)[:1000]


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _distribution(values: Sequence[int]) -> dict[str, int | float | None]:
    if not values:
        return {"count": 0, "average": None, "p50": None, "p90": None}
    ordered = sorted(values)
    p90_index = max(math.ceil(0.9 * len(ordered)) - 1, 0)
    return {
        "count": len(ordered),
        "average": round(sum(ordered) / len(ordered), 2),
        "p50": median(ordered),
        "p90": ordered[p90_index],
    }


def _utc_now_text() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
