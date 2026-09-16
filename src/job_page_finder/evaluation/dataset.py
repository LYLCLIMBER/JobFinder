from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from job_page_finder.evaluation.contracts import EvaluationCase, EvaluationError


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


def _read_jsonl(path: Path, label: str) -> list[tuple[int, object]]:
    path = path.expanduser()
    if not path.is_file():
        raise EvaluationError(f"{label} does not exist: {path}")
    records: list[tuple[int, object]] = []
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


def _validation_message(exc: ValidationError) -> str:
    error = exc.errors()[0]
    location = ".".join(str(part) for part in error.get("loc", ()))
    message = error.get("msg", "invalid")
    return f"{location}: {message}" if location else message
