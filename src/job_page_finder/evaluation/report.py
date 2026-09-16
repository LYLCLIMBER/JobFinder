from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from statistics import median
from typing import Any

from job_page_finder.evaluation.contracts import SCHEMA_VERSION, EvaluationError
from job_page_finder.evaluation.dataset import dataset_fingerprint, read_cases
from job_page_finder.evaluation.store import read_run_records, write_json_atomic, write_text_atomic


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
        success_count = bucket_succeeded[bucket]
        by_bucket[bucket] = {
            "total": bucket_totals[bucket],
            "completed": bucket_completed[bucket],
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


def render_markdown(summary: Mapping[str, Any]) -> str:
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
    return "\n".join(lines)


def write_summary(summary: Mapping[str, Any], json_path: Path, markdown_path: Path) -> None:
    write_json_atomic(json_path, dict(summary))
    write_text_atomic(markdown_path, render_markdown(summary))


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
    from datetime import UTC, datetime

    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
