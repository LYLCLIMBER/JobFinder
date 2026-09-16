from __future__ import annotations

import asyncio
import errno
import fcntl
import json
import logging
import os
import re
from collections.abc import Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, TextIO

from pydantic import ValidationError

from job_page_finder.evaluation.contracts import EvaluationError, EvaluationRunRecord, EvaluationSummary

logger = logging.getLogger(__name__)
_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_RELEASE_TIMEOUT = 1.0


def validate_evaluation_run_id(run_id: str) -> str:
    if not run_id.strip():
        raise EvaluationError("run_id must not be blank")
    if _RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise EvaluationError("run_id must be a single safe path component using letters, digits, '.', '_' or '-'")
    return run_id


def sidecar_path(path: Path, label: str) -> Path:
    return path.with_name(f"{path.stem}.{label}.json")


def _file_sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise EvaluationError(f"could not hash database: {exc}") from exc
    return digest.hexdigest()


class JsonlEvaluationResultStore:
    def __init__(
        self,
        results_path: Path,
        *,
        campaign_id: str,
        dataset_fingerprint: str,
        manifest_metadata: Mapping[str, Any],
    ) -> None:
        self._results_path = results_path.expanduser()
        self._campaign_id = validate_evaluation_run_id(campaign_id)
        self._dataset_fingerprint = dataset_fingerprint
        self._manifest_metadata = dict(manifest_metadata)

    def open_campaign(self) -> _JsonlStoreSession:
        return _JsonlStoreSession(
            self._results_path,
            campaign_id=self._campaign_id,
            dataset_fingerprint=self._dataset_fingerprint,
            manifest_metadata=self._manifest_metadata,
        )


class _JsonlStoreSession:
    def __init__(
        self,
        results_path: Path,
        *,
        campaign_id: str,
        dataset_fingerprint: str,
        manifest_metadata: Mapping[str, Any],
    ) -> None:
        self._results_path = results_path
        self._campaign_id = campaign_id
        self._dataset_fingerprint = dataset_fingerprint
        self._manifest_metadata = dict(manifest_metadata)
        self._records: list[EvaluationRunRecord] = []
        self._stream: TextIO | None = None
        self._lock_cm = _evaluation_lock(results_path)

    async def __aenter__(self) -> _JsonlStoreSession:
        await self._lock_cm.__aenter__()
        try:
            await asyncio.to_thread(_prepare_run_manifest, self._results_path, metadata=self._manifest_metadata)
            await asyncio.to_thread(_repair_torn_final_line, self._results_path)
            self._records = await asyncio.to_thread(
                read_run_records,
                self._results_path,
                expected_fingerprint=self._dataset_fingerprint,
                expected_run_id=self._campaign_id,
            )
            open_task = asyncio.create_task(asyncio.to_thread(_open_append, self._results_path))
            try:
                self._stream = await asyncio.shield(open_task)
            except asyncio.CancelledError:
                current = asyncio.current_task()
                if current is not None and current.cancelling():
                    current.uncancel()
                try:
                    stream = await asyncio.shield(open_task)
                except Exception:
                    logger.warning("evaluation results stream open failed during cancellation", exc_info=True)
                else:
                    await _cleanup_in_thread(stream.close, "evaluation results stream close")
                raise
        except BaseException:
            await self._lock_cm.__aexit__(*_exc_info())
            raise
        return self

    async def __aexit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: object) -> None:
        stream = self._stream
        self._stream = None
        try:
            if stream is not None:
                await _cleanup_in_thread(stream.close, "evaluation results stream close")
        finally:
            await self._lock_cm.__aexit__(exc_type, exc, tb)

    def completed_case_ids(self) -> set[str]:
        return {record.case_id for record in self._records}

    def append(self, record: EvaluationRunRecord) -> None:
        if self._stream is None:
            raise EvaluationError("evaluation store session is not open")
        _append_json_record(self._stream, record.model_dump(mode="json"))
        self._records.append(record)

    def records(self) -> tuple[EvaluationRunRecord, ...]:
        return tuple(self._records)

    def finish(self, summary: EvaluationSummary) -> None:
        manifest_path = sidecar_path(self._results_path, "manifest")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise EvaluationError(f"could not read run manifest: {exc}") from exc
        _write_json_atomic(
            manifest_path,
            {
                **manifest,
                "campaign_status": "completed",
                "campaign_summary": summary.model_dump(mode="json"),
                "completed_at": _utc_now_text(),
            },
        )


def read_run_records(
    path: Path,
    *,
    expected_fingerprint: str | None = None,
    expected_run_id: str | None = None,
) -> list[EvaluationRunRecord]:
    path = path.expanduser()
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


@asynccontextmanager
async def _evaluation_lock(results_path: Path):
    path = sidecar_path(results_path.expanduser(), "lock")
    try:
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
    except OSError as exc:
        raise EvaluationError(f"could not lock evaluation results: {exc}") from exc
    try:
        open_task = asyncio.create_task(asyncio.to_thread(path.open, "a", encoding="utf-8"))
        try:
            lock_file = await asyncio.shield(open_task)
        except asyncio.CancelledError:
            # The file may have been opened just before cancellation reached
            # this task. Retrieve and close it before propagating cancellation.
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                current.uncancel()
            try:
                lock_file = await asyncio.shield(open_task)
            except Exception:
                logger.warning("evaluation lock open failed during cancellation", exc_info=True)
            else:
                await _cleanup_in_thread(lock_file.close, "evaluation lock close")
            raise
    except OSError as exc:
        raise EvaluationError(f"could not lock evaluation results: {exc}") from exc

    lock_acquired = False
    try:
        while True:
            try:
                acquire_task = asyncio.create_task(
                    asyncio.to_thread(fcntl.flock, lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                )
                try:
                    await asyncio.shield(acquire_task)
                except asyncio.CancelledError:
                    current = asyncio.current_task()
                    if current is not None and current.cancelling():
                        current.uncancel()
                    try:
                        await asyncio.shield(acquire_task)
                    except OSError:
                        pass
                    else:
                        lock_acquired = True
                    raise
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
        raise
    finally:

        def release() -> None:
            if lock_acquired:
                try:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                except OSError as exc:
                    logger.warning("could not unlock evaluation results: %s", exc)
            try:
                lock_file.close()
            except OSError as exc:
                logger.warning("could not close evaluation lock: %s", exc)

        current = asyncio.current_task()
        if current is not None and current.cancelling():
            # The cancellation which entered this finally is already being
            # propagated.  Clear its pending delivery long enough to await the
            # cleanup, without replacing the caller's CancelledError.
            current.uncancel()
        await _cleanup_in_thread(release, "evaluation lock release")


async def _cleanup_in_thread(operation: Any, label: str) -> None:
    """Run an uninterruptible synchronous cleanup without delaying the loop."""
    task = asyncio.create_task(asyncio.to_thread(operation))
    cancelled = False
    try:
        while True:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=_RELEASE_TIMEOUT)
                break
            except asyncio.CancelledError:
                # Delay propagating cancellation until the bounded cleanup has
                # had a chance to close the descriptor.
                cancelled = True
                current = asyncio.current_task()
                if current is not None and current.cancelling():
                    current.uncancel()
                continue
    except TimeoutError:
        # Cancelling a worker thread cannot stop its syscall; let it finish so
        # it can still release the descriptor/lock.
        logger.warning("%s timed out; cleanup continues in a worker thread", label)
    except Exception:
        logger.warning("%s failed", label, exc_info=True)
    if cancelled:
        raise asyncio.CancelledError


def _prepare_run_manifest(
    results_path: Path,
    *,
    metadata: Mapping[str, Any],
) -> None:
    path = sidecar_path(results_path, "manifest")
    expected = dict(metadata)
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


def _open_append(path: Path) -> TextIO:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        return path.open("a", encoding="utf-8")
    except OSError as exc:
        raise EvaluationError(f"could not open results file {path}: {exc}") from exc


def _append_json_record(stream: TextIO, record: Mapping[str, Any]) -> None:
    try:
        stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    except OSError as exc:
        raise EvaluationError(f"could not append evaluation result: {exc}") from exc


def write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    _write_json_atomic(path, value)


def write_text_atomic(path: Path, text: str) -> None:
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


def _write_jsonl_atomic(path: Path, records: list[Mapping[str, Any]]) -> None:
    text = "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records)
    _write_text_atomic(path, text)


def _utc_now_text() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _validation_message(exc: ValidationError) -> str:
    error = exc.errors()[0]
    location = ".".join(str(part) for part in error.get("loc", ()))
    message = error.get("msg", "invalid")
    return f"{location}: {message}" if location else message


def _exc_info() -> tuple[type[BaseException], BaseException, object]:
    import sys

    info = sys.exc_info()
    assert info[0] is not None and info[1] is not None
    return info[0], info[1], info[2]
