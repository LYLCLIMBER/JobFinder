import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from job_page_finder import runtime
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
from job_page_finder.runner import TaskResult, build_failed_result, resolve_task_id
from job_page_finder.runtime import RuntimeConfig, run_task

EXIT_SUCCESS = 0
EXIT_TASK_FAILED = 1
EXIT_INVALID_INPUT = 2
EXIT_CONFIGURATION_ERROR = 3


class _CliUsageError(ValueError):
    pass


class _JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _CliUsageError(message)


def _build_parser() -> argparse.ArgumentParser:
    parser = _JsonArgumentParser(prog="jobfinder", description="Run JobFinder tasks")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run a JSON task file")
    run_parser.add_argument("task_file")
    _add_diagnostics_arguments(run_parser)

    find_parser = subparsers.add_parser("find-job-page", help="Find a job listing page")
    find_parser.add_argument("company_url")
    find_parser.add_argument("--max-steps", type=int, default=8)
    _add_diagnostics_arguments(find_parser)

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
    default_root = None if infer_diagnostics_root else (diagnostics_root or runtime.DEFAULT_DIAGNOSTICS_ROOT)
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
    sys.stdout.write(result.model_dump_json(indent=2))
    sys.stdout.write("\n")


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


def _load_task_file(path_value: str) -> tuple[dict[str, object] | None, TaskResult | None]:
    path = Path(path_value)
    if not path.is_file():
        return None, build_failed_result(
            task_id=resolve_task_id(None),
            task_type="unknown",
            code="INVALID_TASK",
            message=f"Task file does not exist: {path}",
            duration_ms=0,
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, build_failed_result(
            task_id=resolve_task_id(None),
            task_type="unknown",
            code="INVALID_TASK",
            message=f"Task file is not valid JSON: {exc.msg}",
            duration_ms=0,
        )
    except (OSError, UnicodeDecodeError) as exc:
        return None, build_failed_result(
            task_id=resolve_task_id(None),
            task_type="unknown",
            code="INVALID_TASK",
            message=f"Task file could not be read: {exc}",
            duration_ms=0,
        )
    if not isinstance(payload, dict):
        return None, build_failed_result(
            task_id=resolve_task_id(None),
            task_type="unknown",
            code="INVALID_TASK",
            message="Task file must contain a JSON object",
            duration_ms=0,
        )
    return payload, None


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
        result = build_failed_result(
            task_id=resolve_task_id(None),
            task_type="unknown",
            code="INVALID_TASK",
            message=str(exc),
            duration_ms=0,
        )
        _print_result(result)
        return EXIT_INVALID_INPUT

    if args.command == "evaluate":
        return _handle_evaluate(args)

    if args.command == "run":
        payload, error_result = _load_task_file(args.task_file)
        if error_result is not None:
            _print_result(error_result)
            return EXIT_INVALID_INPUT
    else:
        payload = {
            "version": "v1",
            "type": "find_job_page",
            "payload": {
                "company_url": args.company_url,
                "max_steps": args.max_steps,
            },
        }

    try:
        config = _runtime_config(args)
    except ValidationError as exc:
        result = build_failed_result(
            task_id=resolve_task_id(None),
            task_type="unknown",
            code="INVALID_TASK",
            message=f"Invalid runtime configuration: {exc.errors()[0]['msg']}",
            duration_ms=0,
        )
        _print_result(result)
        return EXIT_INVALID_INPUT
    result = asyncio.run(run_task(payload, config=config))
    _print_result(result)
    return _exit_code(result)


def run() -> None:
    raise SystemExit(main())
