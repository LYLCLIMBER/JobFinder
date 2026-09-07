"""Per-run diagnostic artifacts, deliberately separate from application logging."""

from __future__ import annotations

import base64
import contextvars
import fcntl
import json
import logging
import os
import re
import shutil
import threading
import uuid
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

DiagnosticLevel = Literal["basic", "diagnostic", "raw"]
_RUN_DIRECTORY_PATTERN = re.compile(r"^\d{8}T\d{12}Z_([0-9a-f]{32})$")
_current_writer: contextvars.ContextVar[DiagnosticWriter | None] = contextvars.ContextVar(
    "diagnostic_writer", default=None
)
_cleanup_lock = threading.Lock()
_FINAL_SUMMARY_RESERVE_BYTES = 8 * 1024
_EXCLUDED_SDK_FIELDS = frozenset(
    {"api_key", "authorization", "cookies", "headers", "reasoning", "reasoning_content", "thinking"}
)


def current_diagnostics() -> DiagnosticWriter | None:
    return _current_writer.get()


def set_current_diagnostics(writer: DiagnosticWriter | None) -> contextvars.Token[DiagnosticWriter | None]:
    return _current_writer.set(writer)


def reset_current_diagnostics(token: contextvars.Token[DiagnosticWriter | None]) -> None:
    _current_writer.reset(token)


class DiagnosticWriter:
    """Best-effort JSONL and artifact writer. Its failures never escape to the task."""

    def __init__(
        self,
        *,
        root: Path,
        level: DiagnosticLevel,
        task_id: str,
        task_type: str,
        capture_screenshots: bool,
        max_runs: int,
        retention_days: int,
        max_run_bytes: int = 256 * 1024 * 1024,
        max_total_bytes: int = 5 * 1024 * 1024 * 1024,
    ) -> None:
        self.run_id = uuid.uuid4().hex
        self.level = level
        self.task_id = task_id
        self.task_type = task_type
        self.capture_screenshots = capture_screenshots
        self.root = root
        self.max_runs = max_runs
        self.retention_days = retention_days
        self.max_run_bytes = max_run_bytes
        self.max_total_bytes = max_total_bytes
        self.run_dir: Path | None = None
        self._artifacts: list[dict[str, str]] = []
        self._failed: str | None = None
        self._manifest: dict[str, Any] = {
            "run_id": self.run_id,
            "task_id": task_id,
            "task_type": task_type,
            "diagnostic_level": level,
            "redaction_applied": False,
            "capture_screenshots": capture_screenshots,
            "max_run_bytes": max_run_bytes,
            "max_total_bytes": max_total_bytes,
            "status": "running",
            "artifacts": self._artifacts,
        }
        self._open()

    @property
    def enabled(self) -> bool:
        return self.run_dir is not None

    @property
    def is_diagnostic(self) -> bool:
        return self.level in {"diagnostic", "raw"}

    @property
    def is_raw(self) -> bool:
        return self.level == "raw"

    def _open(self) -> None:
        try:
            with _cleanup_lock:
                self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
                self.root.chmod(0o700)
                with self._filesystem_lock():
                    runs = self._cleanup(exclude=set(), reserve_slot=True)
                    if len(runs) >= self.max_runs:
                        raise OSError(f"diagnostic capacity reached (max_runs={self.max_runs})")
                    self.run_dir = self.root / f"{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}_{self.run_id}"
                    self.run_dir.mkdir(mode=0o700)
                    self._write_manifest()
            self.event("run_started")
        except Exception as exc:
            self._fail(exc)

    @contextmanager
    def _filesystem_lock(self):
        """Serialize cleanup and directory reservation across local processes."""
        lock_path = self.root / ".diagnostics.lock"
        with lock_path.open("a", encoding="utf-8") as lock_file:
            lock_path.chmod(0o600)
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _cleanup(self, *, exclude: set[Path], reserve_slot: bool) -> list[Path]:
        cutoff = datetime.now(UTC) - timedelta(days=self.retention_days)
        completed = self._completed_runs()
        for path in completed:
            if path in exclude or datetime.fromtimestamp(path.stat().st_mtime, UTC) >= cutoff:
                continue
            self._remove_run(path)
        completed = self._completed_runs()
        if reserve_slot:
            for path in completed:
                if len(self._managed_runs()) < self.max_runs:
                    break
                if path not in exclude:
                    self._remove_run(path)
        return self._managed_runs()

    def _managed_runs(self) -> list[Path]:
        return [path for path in self.root.iterdir() if path.is_dir() and self._read_manifest(path) is not None]

    def _completed_runs(self) -> list[Path]:
        completed: list[Path] = []
        for path in self._managed_runs():
            manifest = self._read_manifest(path)
            if manifest is not None and manifest.get("status") in {"succeeded", "failed", "aborted"}:
                completed.append(path)
        return sorted(completed, key=lambda path: path.stat().st_mtime)

    def _read_manifest(self, path: Path) -> dict[str, Any] | None:
        match = _RUN_DIRECTORY_PATTERN.fullmatch(path.name)
        if match is None:
            return None
        suffix = match.group(1)
        try:
            manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if (
            not isinstance(manifest, dict)
            or manifest.get("run_id") != suffix
            or not isinstance(manifest.get("task_id"), str)
            or not isinstance(manifest.get("task_type"), str)
            or not isinstance(manifest.get("artifacts"), list)
            or manifest.get("redaction_applied") is not False
        ):
            return None
        if manifest.get("diagnostic_level") not in {"basic", "diagnostic", "raw"}:
            return None
        if manifest.get("status") not in {"running", "succeeded", "failed", "aborted"}:
            return None
        return manifest

    def _remove_run(self, path: Path) -> None:
        shutil.rmtree(path)

    def _fail(self, exc: BaseException) -> None:
        if self._failed is None:
            self._failed = f"{type(exc).__name__}: {exc}"
            logger.warning("runtime diagnostics disabled: %s", self._failed)

    def _write_manifest(self) -> None:
        if self.run_dir is None:
            return
        try:
            destination = self.run_dir / "manifest.json"
            temporary = self.run_dir / f".manifest-{uuid.uuid4().hex}.tmp"
            self._atomic_write(
                destination,
                json.dumps(self._manifest, ensure_ascii=False, indent=2, default=str).encode(),
                temporary,
            )
        except Exception as exc:
            self._fail(exc)

    def event(self, name: str, **fields: Any) -> None:
        if self.run_dir is None:
            return
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "event": name,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "task_type": self.task_type,
            **fields,
        }
        try:
            event_path = self.run_dir / "events.jsonl"
            with event_path.open("a", encoding="utf-8") as stream:
                event_path.chmod(0o600)
                stream.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
        except Exception as exc:
            self._fail(exc)

    def artifact(self, name: str, value: Any, *, event: str | None = None, **references: str) -> str | None:
        if self.run_dir is None:
            return None
        path = self.run_dir / "artifacts" / f"{len(self._artifacts) + 1:03d}_{name}.json"
        try:
            data = json.dumps(value, ensure_ascii=False, indent=2, default=str).encode()
            if not self._write_artifact(path, data, name):
                return None
            relative = str(path.relative_to(self.run_dir))
            self._artifacts.append({"path": relative, "kind": name, **references})
            self._write_manifest()
            if event:
                self.event(event, artifact=relative, **references)
            return relative
        except Exception as exc:
            self._fail(exc)
            return None

    def screenshot(self, name: str, encoded: str | None, *, event: str, **references: str) -> str | None:
        if self.run_dir is None or not self.capture_screenshots or not encoded:
            return None
        path = self.run_dir / "artifacts" / f"{len(self._artifacts) + 1:03d}_{name}.png"
        try:
            if not self._write_artifact(path, base64.b64decode(encoded), name):
                return None
            relative = str(path.relative_to(self.run_dir))
            self._artifacts.append({"path": relative, "kind": name, **references})
            self._write_manifest()
            self.event(event, artifact=relative, **references)
            return relative
        except Exception as exc:
            self._fail(exc)
            return None

    def _write_artifact(self, path: Path, data: bytes, name: str) -> bool:
        try:
            with _cleanup_lock, self._filesystem_lock():
                if not self._has_artifact_capacity(len(data), reserve=name != "result"):
                    self._manifest["diagnostic_incomplete"] = True
                    if name == "result":
                        self._manifest["summary_budget_exceeded"] = True
                    else:
                        self._manifest.setdefault("omitted_artifacts", []).append(
                            {"kind": name, "bytes": len(data), "reason": "capacity_limit"}
                        )
                        self._write_manifest()
                        self.event("artifact_omitted", kind=name, bytes=len(data), reason="capacity_limit")
                        return False
                path.parent.mkdir(exist_ok=True, mode=0o700)
                path.parent.chmod(0o700)
                self._atomic_write(path, data)
            return True
        except Exception as exc:
            self._fail(exc)
            return False

    def _has_artifact_capacity(self, size: int, *, reserve: bool) -> bool:
        if self.run_dir is None:
            return False
        run_bytes = self._directory_bytes(self.run_dir)
        total_bytes = sum(self._directory_bytes(path) for path in self._managed_runs())
        reserved_bytes = _FINAL_SUMMARY_RESERVE_BYTES if reserve else 0
        return (
            run_bytes + size <= self.max_run_bytes - reserved_bytes
            and total_bytes + size <= self.max_total_bytes - reserved_bytes
        )

    @staticmethod
    def _directory_bytes(path: Path) -> int:
        try:
            return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
        except OSError:
            return 0

    @staticmethod
    def _atomic_write(destination: Path, data: bytes, temporary: Path | None = None) -> None:
        temporary = temporary or destination.with_name(f".{destination.name}-{uuid.uuid4().hex}.tmp")
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
            temporary.replace(destination)
            destination.chmod(0o600)
        finally:
            if temporary.exists():
                temporary.unlink()

    def finish(self, result: Mapping[str, Any]) -> None:
        if self.run_dir is None:
            return
        self.artifact("result", dict(result), event="result_written")
        self._manifest["status"] = result.get("status", "failed")
        self._manifest["finished_at"] = datetime.now(UTC).isoformat()
        if self._failed:
            self._manifest["diagnostic_failure"] = self._failed
        self.event("run_finished", status=self._manifest["status"], diagnostic_failure=self._failed)
        self._write_manifest()
        try:
            with _cleanup_lock, self._filesystem_lock():
                self._cleanup(exclude={self.run_dir}, reserve_slot=False)
        except Exception as exc:
            self._fail(exc)
            self._manifest["diagnostic_failure"] = self._failed
            self.event("diagnostic_cleanup_failed", error=self._failed)
            self._write_manifest()

    def abort(self) -> None:
        if self.run_dir is None:
            return
        self._manifest["status"] = "aborted"
        self._manifest["finished_at"] = datetime.now(UTC).isoformat()
        self.event("run_aborted")
        self._write_manifest()


def serializable(value: Any) -> Any:
    """Capture available SDK objects without pretending to have HTTP-level data."""
    if hasattr(value, "model_dump"):
        try:
            return serializable(value.model_dump(mode="json"))
        except Exception:
            return str(value)
    if isinstance(value, Mapping):
        return {
            str(key): serializable(item) for key, item in value.items() if str(key).lower() not in _EXCLUDED_SDK_FIELDS
        }
    if isinstance(value, (list, tuple)):
        return [serializable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
