from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from job_page_finder.evaluation.contracts import (
    EvaluationCase,
    EvaluationError,
    EvaluationResultStore,
    EvaluationRunRecord,
    EvaluationStoreSession,
    EvaluationSummary,
)
from job_page_finder.task_protocol import TaskResult


class TaskApplication(Protocol):
    async def run(self, request: Mapping[str, Any]) -> TaskResult: ...


class EvaluationCampaign:
    def __init__(
        self,
        application: TaskApplication,
        store: EvaluationResultStore,
        *,
        concurrency: int,
        case_timeout: float,
        campaign_id: str,
        dataset_fingerprint: str,
    ) -> None:
        if concurrency <= 0:
            raise EvaluationError("workers must be positive")
        if not math_isfinite(case_timeout) or case_timeout <= 0:
            raise EvaluationError("case_timeout must be greater than zero")
        self._application = application
        self._store = store
        self._concurrency = concurrency
        self._case_timeout = case_timeout
        self._campaign_id = campaign_id
        self._fingerprint = dataset_fingerprint

    async def run(self, cases: Iterable[EvaluationCase]) -> EvaluationSummary:
        # A campaign needs to traverse cases for both resume filtering and the
        # final summary; consume one-shot iterables before opening the session.
        cases = list(cases)
        async with self._store.open_campaign() as session:
            completed_ids = session.completed_case_ids()
            pending = [case for case in cases if case.case_id not in completed_ids]
            if pending:
                await self._run_workers(session, pending)
            summary = _summary(cases, session.records())
            session.finish(summary)
            return summary

    async def _run_workers(self, session: EvaluationStoreSession, pending: Sequence[EvaluationCase]) -> None:
        queue: asyncio.Queue[EvaluationCase | None] = asyncio.Queue()
        result_queue: asyncio.Queue[EvaluationRunRecord] = asyncio.Queue()
        for case in pending:
            queue.put_nowait(case)
        for _ in range(min(self._concurrency, len(pending))):
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
                            run_id=self._campaign_id,
                            fingerprint=self._fingerprint,
                            case_timeout=self._case_timeout,
                            application=self._application,
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        now = datetime.now(UTC)
                        record = EvaluationRunRecord(
                            run_id=self._campaign_id,
                            dataset_fingerprint=self._fingerprint,
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

        worker_tasks = [asyncio.create_task(worker()) for _ in range(min(self._concurrency, len(pending)))]
        try:
            for _ in pending:
                record = await result_queue.get()
                session.append(record)
                result_queue.task_done()
            await queue.join()
            await asyncio.gather(*worker_tasks)
        finally:
            for task in worker_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*worker_tasks, return_exceptions=True)


async def _run_case(
    case: EvaluationCase,
    *,
    run_id: str,
    fingerprint: str,
    case_timeout: float,
    application: TaskApplication,
) -> EvaluationRunRecord:
    started_at = datetime.now(UTC)
    started = time.perf_counter()
    try:
        result = await asyncio.wait_for(application.run(case.task_request()), timeout=case_timeout)
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


def _clean_error(exc: BaseException) -> str:
    message = " ".join(str(exc).replace("\r", " ").replace("\n", " ").split())
    return (f"{type(exc).__name__}: {message}" if message else type(exc).__name__)[:1000]


def math_isfinite(value: float) -> bool:
    import math

    return math.isfinite(value)


def _summary(cases: Sequence[EvaluationCase], records: Sequence[EvaluationRunRecord]) -> EvaluationSummary:
    succeeded = sum(
        record.finder_result is not None and record.finder_result.status == "succeeded" for record in records
    )
    completed = len(records)
    return EvaluationSummary(
        total=len(cases),
        completed=completed,
        succeeded=succeeded,
        failed=completed - succeeded,
        success_rate=round(succeeded / len(cases), 6) if cases else None,
    )
