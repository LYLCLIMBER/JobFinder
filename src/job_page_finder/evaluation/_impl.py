from __future__ import annotations

import math
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

from job_page_finder.evaluation.adapters.corpweb_sqlite import CorpWebSqliteCaseSource
from job_page_finder.evaluation.campaign import EvaluationCampaign
from job_page_finder.evaluation.contracts import SCHEMA_VERSION, EvaluationError
from job_page_finder.evaluation.dataset import dataset_fingerprint, read_cases
from job_page_finder.evaluation.report import summarize
from job_page_finder.evaluation.store import (
    JsonlEvaluationResultStore,
    _file_sha256,
    _utc_now_text,
    _write_json_atomic,
    _write_jsonl_atomic,
    sidecar_path,
    validate_evaluation_run_id,
)
from job_page_finder.runtime import RuntimeConfig, build_application, settings_from_config
from job_page_finder.task_protocol import TaskResult

DEFAULT_SAMPLE_SIZE = 120
DEFAULT_SAMPLE_SEED = "jobfinder-pilot-v1"

RunTask = Callable[[Mapping[str, Any], RuntimeConfig], Awaitable[TaskResult]]


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

    source = CorpWebSqliteCaseSource(db_path, max_steps=max_steps)
    population, rejected = source.load_population()
    if sample_size > len(population):
        raise EvaluationError(f"sample_size {sample_size} exceeds eligible population {len(population)}")
    selected = source.sample(population, sample_size=sample_size, seed=seed)
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
    _write_json_atomic(manifest_path or sidecar_path(output_path, "manifest"), manifest)
    return manifest


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
    cases = read_cases(dataset_path)
    fingerprint = dataset_fingerprint(cases)
    runtime_config = config or RuntimeConfig()
    store = JsonlEvaluationResultStore(
        results_path,
        campaign_id=run_id,
        dataset_fingerprint=fingerprint,
        manifest_metadata=_run_manifest_metadata(
            run_id=run_id,
            dataset_fingerprint_value=fingerprint,
            workers=workers,
            case_timeout=case_timeout,
            runtime_config=runtime_config,
        ),
    )
    application = (
        _InjectedApplication(run_task_fn, runtime_config)
        if run_task_fn
        else build_application(
            settings_from_config(runtime_config),
        )
    )

    campaign = EvaluationCampaign(
        application,
        store,
        concurrency=workers,
        case_timeout=case_timeout,
        campaign_id=run_id,
        dataset_fingerprint=fingerprint,
    )
    await campaign.run(cases)
    return summarize(dataset_path, results_path)


class _InjectedApplication:
    def __init__(self, execute: RunTask, config: RuntimeConfig) -> None:
        self._execute = execute
        self._config = config

    async def run(self, request: Mapping[str, Any]) -> TaskResult:
        return await self._execute(request, self._config)


def _run_manifest_metadata(
    *,
    run_id: str,
    dataset_fingerprint_value: str,
    workers: int,
    case_timeout: float,
    runtime_config: RuntimeConfig,
) -> Mapping[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "purpose": "operational_evaluation",
        "run_id": run_id,
        "dataset_fingerprint": dataset_fingerprint_value,
        "workers": workers,
        "case_timeout_seconds": case_timeout,
        "runtime_config": runtime_config.model_dump(mode="json"),
    }
