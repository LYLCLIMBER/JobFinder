from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path

from pydantic import ValidationError

from job_page_finder.evaluation import (
    DEFAULT_SAMPLE_SEED,
    DEFAULT_SAMPLE_SIZE,
    EvaluationError,
    generate_corpweb_dataset,
    run_evaluation,
    summarize,
    validate_evaluation_run_id,
    write_summary,
)
from job_page_finder.runner import resolve_task_id
from job_page_finder.runtime import RuntimeConfig, build_application, settings_from_config
from job_page_finder.settings import RuntimeSettings
from job_page_finder.task_protocol import TaskResult, build_task_failure

EXIT_SUCCESS = 0
EXIT_TASK_FAILED = 1
EXIT_INVALID_INPUT = 2
EXIT_CONFIGURATION_ERROR = 3
_EXIT_PRIORITY = {
    EXIT_SUCCESS: 0,
    EXIT_TASK_FAILED: 1,
    EXIT_INVALID_INPUT: 2,
    EXIT_CONFIGURATION_ERROR: 3,
}


class _CliUsageError(ValueError):
    pass


class _JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _CliUsageError(message)


def _build_parser() -> argparse.ArgumentParser:
    parser = _JsonArgumentParser(prog="jobfinder", description="Run JobFinder tasks")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run TaskRequest JSONL from a file or stdin")
    run_parser.add_argument("input_path", nargs="?", default=None)
    _add_diagnostics_arguments(run_parser)

    evaluate_parser = subparsers.add_parser("evaluate", help="Generate and run operational evaluations")
    evaluate_subparsers = evaluate_parser.add_subparsers(dest="evaluate_command", required=True)

    generate_parser = evaluate_subparsers.add_parser("generate", help="Sample valid CorpWeb company sites")
    generate_parser.add_argument("--db", type=Path, required=True)
    generate_parser.add_argument("--output", type=Path, required=True)
    generate_parser.add_argument("--manifest", type=Path)
    generate_parser.add_argument("--sample-size", type=int, default=DEFAULT_SAMPLE_SIZE)
    generate_parser.add_argument("--seed", default=DEFAULT_SAMPLE_SEED)
    generate_parser.add_argument("--max-steps", type=int, default=8)

    evaluate_run_parser = evaluate_subparsers.add_parser("run", help="Run JobFinder over an evaluation dataset")
    evaluate_run_parser.add_argument("--dataset", type=Path, required=True)
    evaluate_run_parser.add_argument("--results", type=Path)
    evaluate_run_parser.add_argument("--run-id", required=True)
    evaluate_run_parser.add_argument("--workers", type=int, default=2)
    evaluate_run_parser.add_argument("--case-timeout", type=float, default=300)
    _add_diagnostics_arguments(
        evaluate_run_parser,
        diagnostics_level="diagnostic",
        infer_diagnostics_root=True,
    )

    summarize_parser = evaluate_subparsers.add_parser("summarize", help="Summarize evaluation results")
    summarize_parser.add_argument("--dataset", type=Path, required=True)
    summarize_parser.add_argument("--results", type=Path, required=True)
    summarize_parser.add_argument("--json-output", type=Path, required=True)
    summarize_parser.add_argument("--markdown-output", type=Path, required=True)
    return parser


def _add_diagnostics_arguments(
    parser: argparse.ArgumentParser,
    *,
    diagnostics_root: Path | None = None,
    diagnostics_level: str = "basic",
    infer_diagnostics_root: bool = False,
) -> None:
    parser.add_argument("--diagnostics-level", choices=("basic", "diagnostic", "raw"), default=diagnostics_level)
    from job_page_finder import settings as settings_module

    default_root = None if infer_diagnostics_root else (diagnostics_root or settings_module.DEFAULT_DIAGNOSTICS_ROOT)
    parser.add_argument("--diagnostics-root", type=Path, default=default_root)
    parser.add_argument("--diagnostics-screenshots", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--diagnostics-max-runs", type=int, default=100)
    parser.add_argument("--diagnostics-retention-days", type=int, default=7)
    parser.add_argument("--diagnostics-max-run-bytes", type=int, default=256 * 1024 * 1024)
    parser.add_argument("--diagnostics-max-total-bytes", type=int, default=5 * 1024 * 1024 * 1024)


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
        force=False,
    )


def _print_result(result: TaskResult) -> None:
    sys.stdout.write(result.model_dump_json())
    sys.stdout.write("\n")
    sys.stdout.flush()


def _print_json(value: object) -> None:
    sys.stdout.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
    sys.stdout.write("\n")


def _exit_code(result: TaskResult) -> int:
    if result.status == "succeeded":
        return EXIT_SUCCESS
    code = result.error.code if result.error is not None else "INTERNAL_ERROR"
    if code in {"INVALID_TASK", "UNSUPPORTED_TASK_TYPE"}:
        return EXIT_INVALID_INPUT
    if code == "CONFIGURATION_ERROR":
        return EXIT_CONFIGURATION_ERROR
    return EXIT_TASK_FAILED


def _merge_exit_code(current: int, result: TaskResult) -> int:
    code = _exit_code(result)
    if _EXIT_PRIORITY[code] > _EXIT_PRIORITY[current]:
        return code
    return current


def _invalid_input(message: str) -> TaskResult:
    return build_task_failure(
        task_id=resolve_task_id(None),
        task_type="unknown",
        code="INVALID_TASK",
        message=message,
        duration_ms=0,
    )


def _parse_json_object(raw: str, *, kind: str) -> dict[str, object] | TaskResult:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return _invalid_input(f"{kind} is not valid JSON: {exc.msg}")
    if not isinstance(payload, dict):
        return _invalid_input(f"{kind} must contain a JSON object")
    return payload


def _iter_jsonl_lines(path_value: str | None) -> Iterator[str | TaskResult]:
    if path_value is None or path_value == "-":
        try:
            yield from _non_empty_lines(sys.stdin)
        except (UnicodeDecodeError, OSError) as exc:
            yield _invalid_input(f"Task input could not be read: {exc}")
        return
    path = Path(path_value)
    if not path.is_file():
        yield _invalid_input(f"Task file does not exist: {path}")
        return
    try:
        with path.open(encoding="utf-8") as handle:
            yield from _non_empty_lines(handle)
    except UnicodeDecodeError as exc:
        yield _invalid_input(f"Task file could not be read: {exc}")
    except OSError as exc:
        yield _invalid_input(f"Task file could not be read: {exc}")


def _non_empty_lines(stream: Iterable[str]) -> Iterator[str]:
    for line in stream:
        if line.strip():
            yield line


async def _run_payloads(
    payloads: Iterable[dict[str, object] | TaskResult],
    settings: RuntimeSettings,
    *,
    diagnostics_screenshots: bool | None = None,
) -> int:
    # Kept for callers of this internal helper from older integrations; normal
    # configuration now carries the override in DiagnosticsSettings.
    if diagnostics_screenshots is not None:
        settings = settings.model_copy(
            update={
                "diagnostics": settings.diagnostics.model_copy(update={"capture_screenshots": diagnostics_screenshots})
            }
        )
    application = build_application(settings)
    exit_code = EXIT_SUCCESS
    for payload in payloads:
        result = payload if isinstance(payload, TaskResult) else await application.run(payload)
        _print_result(result)
        exit_code = _merge_exit_code(exit_code, result)
    return exit_code


def _jsonl_payloads(path_value: str | None) -> Iterator[dict[str, object] | TaskResult]:
    for item in _iter_jsonl_lines(path_value):
        if isinstance(item, TaskResult):
            yield item
        else:
            yield _parse_json_object(item, kind="Task line")


def _runtime_config(args: argparse.Namespace) -> RuntimeConfig:
    return RuntimeConfig(
        diagnostics_level=args.diagnostics_level,
        diagnostics_root=args.diagnostics_root,
        diagnostics_screenshots=args.diagnostics_screenshots,
        diagnostics_max_runs=args.diagnostics_max_runs,
        diagnostics_retention_days=args.diagnostics_retention_days,
        diagnostics_max_run_bytes=args.diagnostics_max_run_bytes,
        diagnostics_max_total_bytes=args.diagnostics_max_total_bytes,
    )


def _evaluation_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    dataset = args.dataset.expanduser()
    validate_evaluation_run_id(args.run_id)
    run_root = dataset.parent / "runs" / args.run_id
    results = (args.results or run_root / "results.jsonl").expanduser()
    diagnostics_root = (args.diagnostics_root or run_root / f"diagnostics-{args.diagnostics_level}").expanduser()
    return results, diagnostics_root


def _evaluation_error(message: str) -> dict[str, object]:
    return {
        "status": "failed",
        "error": {"code": "INVALID_EVALUATION", "message": message},
    }


def _handle_evaluate(args: argparse.Namespace) -> int:
    try:
        if args.evaluate_command == "generate":
            manifest = generate_corpweb_dataset(
                args.db,
                args.output,
                sample_size=args.sample_size,
                seed=args.seed,
                max_steps=args.max_steps,
                manifest_path=args.manifest,
            )
            _print_json(manifest)
            return EXIT_SUCCESS
        if args.evaluate_command == "run":
            results_path, diagnostics_root = _evaluation_paths(args)
            args.diagnostics_root = diagnostics_root
            config = _runtime_config(args)
            summary = asyncio.run(
                run_evaluation(
                    args.dataset,
                    results_path,
                    run_id=args.run_id,
                    workers=args.workers,
                    case_timeout=args.case_timeout,
                    config=config,
                )
            )
            _print_json(summary)
            return EXIT_SUCCESS
        summary = summarize(args.dataset, args.results)
        write_summary(summary, args.json_output, args.markdown_output)
        _print_json(summary)
        return EXIT_SUCCESS
    except (EvaluationError, ValidationError) as exc:
        message = str(exc)
        if isinstance(exc, ValidationError):
            message = f"Invalid runtime configuration: {exc.errors()[0]['msg']}"
        _print_json(_evaluation_error(message))
        return EXIT_INVALID_INPUT


def main(argv: Sequence[str] | None = None) -> int:
    _configure_logging()
    parser = _build_parser()
    try:
        args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    except _CliUsageError as exc:
        _print_result(_invalid_input(str(exc)))
        return EXIT_INVALID_INPUT

    if args.command == "evaluate":
        return _handle_evaluate(args)

    try:
        settings = settings_from_config(_runtime_config(args))
    except ValidationError as exc:
        result = _invalid_input(f"Invalid runtime configuration: {exc.errors()[0]['msg']}")
        _print_result(result)
        return EXIT_INVALID_INPUT

    return asyncio.run(_run_payloads(_jsonl_payloads(args.input_path), settings))


def run() -> None:
    raise SystemExit(main())
