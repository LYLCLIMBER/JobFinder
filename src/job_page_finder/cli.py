import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from job_page_finder import runtime
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
    return parser


def _add_diagnostics_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--diagnostics-level", choices=("basic", "diagnostic", "raw"), default="basic")
    parser.add_argument("--diagnostics-root", type=Path, default=runtime.DEFAULT_DIAGNOSTICS_ROOT)
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
        config = RuntimeConfig(
            diagnostics_level=args.diagnostics_level,
            diagnostics_root=args.diagnostics_root,
            diagnostics_screenshots=args.diagnostics_screenshots,
            diagnostics_max_runs=args.diagnostics_max_runs,
            diagnostics_retention_days=args.diagnostics_retention_days,
            diagnostics_max_run_bytes=args.diagnostics_max_run_bytes,
            diagnostics_max_total_bytes=args.diagnostics_max_total_bytes,
        )
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
