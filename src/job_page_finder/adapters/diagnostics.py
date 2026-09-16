from __future__ import annotations

from pathlib import Path
from typing import Literal

from job_page_finder.diagnostic_event_bridge import FileFinderEventSink
from job_page_finder.diagnostics import DiagnosticWriter, reset_current_diagnostics, set_current_diagnostics
from job_page_finder.ports import FinderEventSink, RunDiagnostics
from job_page_finder.task_protocol import TaskResult

DiagnosticLevel = Literal["basic", "diagnostic", "raw"]


class FileRunDiagnostics:
    def __init__(self, writer: DiagnosticWriter) -> None:
        self._writer = writer
        self._finder_events: FileFinderEventSink | None = None
        self._token = set_current_diagnostics(writer)

    @property
    def finder_events(self) -> FinderEventSink:
        if self._finder_events is None:
            self._finder_events = FileFinderEventSink(self._writer)
        return self._finder_events

    def task_started(self) -> None:
        self._writer.event("task_started")

    def task_finished(self, result: TaskResult) -> None:
        self._writer.finish(result.model_dump(mode="json"))

    def abort(self) -> None:
        self._writer.abort()

    def close(self) -> None:
        reset_current_diagnostics(self._token)


class FileDiagnosticsFactory:
    def __init__(
        self,
        *,
        root: Path,
        level: DiagnosticLevel = "basic",
        capture_screenshots: bool | None = None,
        max_runs: int = 100,
        retention_days: int = 7,
        max_run_bytes: int = 256 * 1024 * 1024,
        max_total_bytes: int = 5 * 1024 * 1024 * 1024,
    ) -> None:
        self._root = root
        self._level = level
        self._capture_screenshots = capture_screenshots
        self._max_runs = max_runs
        self._retention_days = retention_days
        self._max_run_bytes = max_run_bytes
        self._max_total_bytes = max_total_bytes

    def create(self, *, task_id: str, task_type: str) -> FileRunDiagnostics:
        capture_screenshots = (
            self._capture_screenshots if self._capture_screenshots is not None else self._level in {"diagnostic", "raw"}
        )
        writer = DiagnosticWriter(
            root=self._root,
            level=self._level,
            task_id=task_id,
            task_type=task_type,
            capture_screenshots=capture_screenshots,
            max_runs=self._max_runs,
            retention_days=self._retention_days,
            max_run_bytes=self._max_run_bytes,
            max_total_bytes=self._max_total_bytes,
        )
        return FileRunDiagnostics(writer)


def as_run_diagnostics(diagnostics: RunDiagnostics | DiagnosticWriter | None) -> RunDiagnostics:
    if diagnostics is None:
        from job_page_finder.null_diagnostics import NullRunDiagnostics

        return NullRunDiagnostics()
    if isinstance(diagnostics, DiagnosticWriter):
        return FileRunDiagnostics(diagnostics)
    return diagnostics
