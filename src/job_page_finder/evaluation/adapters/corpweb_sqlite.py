from __future__ import annotations

import hashlib
import math
import sqlite3
from collections import defaultdict, deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from job_page_finder.evaluation.contracts import EvaluationCase, EvaluationError


@dataclass(frozen=True)
class CorpWebCandidate:
    case: EvaluationCase
    industry: str


class CorpWebSqliteCaseSource:
    def __init__(self, database: Path, *, max_steps: int = 8) -> None:
        self._database = Path(database).expanduser().resolve()
        self._max_steps = max_steps

    def load(self) -> Iterable[EvaluationCase]:
        population, _rejected = self.load_population()
        return [candidate.case for candidate in population]

    def load_population(self) -> tuple[list[CorpWebCandidate], int]:
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(f"{self._database.as_uri()}?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
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
            if connection is not None:
                connection.close()

        candidates: list[CorpWebCandidate] = []
        rejected = 0
        for row in rows:
            name = row["company_name"] or row["stock_name"]
            exchange = row["exchange"]
            board = row["board"]
            try:
                candidates.append(
                    CorpWebCandidate(
                        case=EvaluationCase(
                            case_id=f"corpweb:{exchange}:{row['stock_code']}",
                            company_name=name,
                            company_url=row["final_url"],
                            source="corpweb",
                            sample_bucket=f"{exchange}:{board}",
                            max_steps=self._max_steps,
                        ),
                        industry=row["industry"] or "UNKNOWN",
                    )
                )
            except ValidationError:
                rejected += 1
        return candidates, rejected

    def sample(self, population: Sequence[CorpWebCandidate], *, sample_size: int, seed: str) -> list[EvaluationCase]:
        return _stratified_sample(population, sample_size=sample_size, seed=seed)


def _stratified_sample(cases: Sequence[CorpWebCandidate], *, sample_size: int, seed: str) -> list[EvaluationCase]:
    strata: dict[str, list[CorpWebCandidate]] = defaultdict(list)
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


def _sample_diverse_industries(cases: Sequence[CorpWebCandidate], count: int, *, seed: str) -> list[EvaluationCase]:
    industries: dict[str, deque[CorpWebCandidate]] = defaultdict(deque)
    grouped: dict[str, list[CorpWebCandidate]] = defaultdict(list)
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


def _stable_key(seed: str, value: str) -> str:
    return hashlib.sha256(f"{seed}:{value}".encode()).hexdigest()
