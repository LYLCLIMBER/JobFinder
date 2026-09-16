from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from typing import Protocol

from job_page_finder.finder import JobPageFinder
from job_page_finder.null_diagnostics import NullDiagnosticsFactory
from job_page_finder.ports import DiagnosticsFactory, RunDiagnostics
from job_page_finder.task_protocol import (
    TaskProtocolError,
    TaskRequest,
    TaskResult,
    build_task_failure,
    build_task_result,
    parse_task,
    to_finder_request,
)

logger = logging.getLogger(__name__)


class FinderProvider(Protocol):
    def get(self) -> JobPageFinder: ...


class TaskApplication:
    def __init__(
        self,
        finder_provider: FinderProvider,
        diagnostics_factory: DiagnosticsFactory | None = None,
    ) -> None:
        self._finder_provider = finder_provider
        self._diagnostics_factory = diagnostics_factory or NullDiagnosticsFactory()

    async def run(
        self,
        request: TaskRequest | Mapping[str, object],
        *,
        emit_started: bool = True,
    ) -> TaskResult:
        started = time.perf_counter()
        run: RunDiagnostics | None = None
        try:
            try:
                parsed = parse_task(request)
            except TaskProtocolError as exc:
                run = self._create_diagnostics(task_id=exc.task_id, task_type=exc.task_type)
                _diagnostic_call(run, "task_started")
                _log_started(exc.task_id, exc.task_type, emit_started=emit_started)
                result = build_task_failure(
                    task_id=exc.task_id,
                    task_type=exc.task_type,
                    code=exc.code,
                    message=exc.public_message,
                    duration_ms=_elapsed_ms(started),
                )
                _log_finished(result)
                _diagnostic_call(run, "task_finished", result)
                return result

            task_id = parsed.task_id or ""
            run = self._create_diagnostics(task_id=task_id, task_type=parsed.type)
            _diagnostic_call(run, "task_started")
            _log_started(task_id, parsed.type, emit_started=emit_started)
            try:
                finder_request = to_finder_request(parsed.payload)
                try:
                    finder = self._finder_provider.get()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    result = build_task_failure(
                        task_id=task_id,
                        task_type=parsed.type,
                        code="CONFIGURATION_ERROR",
                        message=_exception_message(exc),
                        duration_ms=_elapsed_ms(started),
                    )
                else:
                    result = build_task_result(
                        parsed,
                        await finder.find(finder_request, events=_finder_events(run)),
                        duration_ms=_elapsed_ms(started),
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                result = build_task_failure(
                    task_id=task_id,
                    task_type=parsed.type,
                    code="INTERNAL_ERROR",
                    message=_exception_message(exc),
                    duration_ms=_elapsed_ms(started),
                )
            _log_finished(result)
            _diagnostic_call(run, "task_finished", result)
            return result
        except asyncio.CancelledError:
            if run is not None:
                _diagnostic_call(run, "abort", suppress_base_exception=True)
            raise
        finally:
            if run is not None:
                _diagnostic_call(run, "close", suppress_base_exception=True)

    def _create_diagnostics(self, *, task_id: str, task_type: str) -> RunDiagnostics:
        try:
            return self._diagnostics_factory.create(task_id=task_id, task_type=task_type)
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise
            logger.warning("Diagnostics creation cancelled itself", exc_info=True)
            return NullDiagnosticsFactory().create(task_id=task_id, task_type=task_type)
        except Exception:
            logger.warning("Diagnostics creation failed", exc_info=True)
            return NullDiagnosticsFactory().create(task_id=task_id, task_type=task_type)


def _log_started(task_id: str, task_type: str, *, emit_started: bool) -> None:
    if emit_started:
        logger.info("task started task_id=%s task_type=%s", task_id, task_type)


def _log_finished(result: TaskResult) -> None:
    error_code = result.error.code if result.error is not None else None
    logger.info(
        "task finished task_id=%s task_type=%s status=%s duration_ms=%s error_code=%s",
        result.task_id,
        result.type,
        result.status,
        result.metadata.duration_ms,
        error_code,
    )


def _exception_message(exc: BaseException) -> str:
    public_message = getattr(exc, "public_message", None)
    if isinstance(public_message, str) and public_message.strip():
        return public_message.strip()
    message = str(exc).strip()
    name = type(exc).__name__
    if not message:
        return name
    return f"{name}: {message}"


def _elapsed_ms(started: float) -> int:
    return max(int((time.perf_counter() - started) * 1000), 0)


def _finder_events(run: RunDiagnostics):
    try:
        return run.finder_events
    except asyncio.CancelledError:
        current = asyncio.current_task()
        if current is not None and current.cancelling():
            raise
        logger.warning("Diagnostics finder event sink retrieval cancelled itself", exc_info=True)
        return NullDiagnosticsFactory().create(task_id="", task_type="").finder_events
    except Exception:
        logger.warning("Diagnostics finder event sink retrieval failed", exc_info=True)
        return NullDiagnosticsFactory().create(task_id="", task_type="").finder_events


def _diagnostic_call(run: RunDiagnostics, name: str, *args: object, suppress_base_exception: bool = False) -> None:
    method = getattr(run, name, None)
    if not callable(method):
        return
    try:
        method(*args)
    except asyncio.CancelledError:
        current = asyncio.current_task()
        if not suppress_base_exception and current is not None and current.cancelling():
            raise
        logger.warning("Diagnostics %s cancelled", name, exc_info=True)
    except Exception:
        logger.warning("Diagnostics %s failed", name, exc_info=True)
