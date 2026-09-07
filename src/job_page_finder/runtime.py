import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal

from browser_use import BrowserSession, Tools
from browser_use.llm.base import BaseChatModel
from pydantic import BaseModel, ConfigDict, Field

from job_page_finder.config import create_deepseek_llm
from job_page_finder.diagnostics import DiagnosticWriter
from job_page_finder.finder import JobPageFinder
from job_page_finder.runner import (
    TaskRequest,
    TaskResult,
    TaskRunner,
    _elapsed_ms,
    _exception_message,
    _InvalidTaskError,
    _UnsupportedTaskTypeError,
    build_failed_result,
    log_task_finished,
    log_task_started,
    request_identity,
)

DEFAULT_DIAGNOSTICS_ROOT = Path("log/diagnostics")


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_timeout: float = Field(default=30, gt=0)
    startup_timeout: float = Field(default=30, gt=0)
    max_consecutive_failures: int = Field(default=2, ge=1)
    max_dom_characters: int = Field(default=40_000, ge=1)
    use_vision: bool = True
    max_visual_candidates: int = Field(default=20, ge=1)
    diagnostics_level: Literal["basic", "diagnostic", "raw"] = "basic"
    diagnostics_root: Path = Field(default_factory=lambda: DEFAULT_DIAGNOSTICS_ROOT)
    diagnostics_screenshots: bool | None = None
    diagnostics_max_runs: int = Field(default=100, ge=1)
    diagnostics_retention_days: int = Field(default=7, ge=0)
    diagnostics_max_run_bytes: int = Field(default=256 * 1024 * 1024, ge=1)
    diagnostics_max_total_bytes: int = Field(default=5 * 1024 * 1024 * 1024, ge=1)


def create_runner(
    *,
    config: RuntimeConfig | None = None,
    llm: BaseChatModel | None = None,
    tools: Tools | None = None,
    browser_factory: Callable[[], BrowserSession] | None = None,
    finder: JobPageFinder | None = None,
    env_file: str | Path | None = None,
) -> TaskRunner:
    runtime_config = config or RuntimeConfig()
    if finder is None:
        if llm is None:
            llm = create_deepseek_llm(env_file=env_file)
        finder = JobPageFinder(
            llm=llm,
            browser_factory=browser_factory,
            tools=tools,
            max_consecutive_failures=runtime_config.max_consecutive_failures,
            step_timeout=runtime_config.step_timeout,
            startup_timeout=runtime_config.startup_timeout,
            max_dom_characters=runtime_config.max_dom_characters,
            use_vision=runtime_config.use_vision,
            max_visual_candidates=runtime_config.max_visual_candidates,
        )
    return TaskRunner(finder)


async def run_task(
    request: TaskRequest | Mapping[str, Any],
    *,
    runner: TaskRunner | None = None,
    config: RuntimeConfig | None = None,
    llm: BaseChatModel | None = None,
    tools: Tools | None = None,
    browser_factory: Callable[[], BrowserSession] | None = None,
    finder: JobPageFinder | None = None,
    env_file: str | Path | None = None,
) -> TaskResult:
    started = time.perf_counter()
    task_id, task_type = request_identity(request)
    runtime_config = config or RuntimeConfig()
    capture_screenshots = (
        runtime_config.diagnostics_screenshots
        if runtime_config.diagnostics_screenshots is not None
        else runtime_config.diagnostics_level in {"diagnostic", "raw"}
    )
    diagnostics = DiagnosticWriter(
        root=runtime_config.diagnostics_root,
        level=runtime_config.diagnostics_level,
        task_id=task_id,
        task_type=task_type,
        capture_screenshots=capture_screenshots,
        max_runs=runtime_config.diagnostics_max_runs,
        retention_days=runtime_config.diagnostics_retention_days,
        max_run_bytes=runtime_config.diagnostics_max_run_bytes,
        max_total_bytes=runtime_config.diagnostics_max_total_bytes,
    )
    log_task_started(task_id, task_type)
    try:
        parsed = TaskRunner.parse_request(request)
    except _UnsupportedTaskTypeError as exc:
        result = build_failed_result(
            task_id=task_id,
            task_type=exc.task_type,
            code="UNSUPPORTED_TASK_TYPE",
            message=str(exc),
            duration_ms=_elapsed_ms(started),
        )
        log_task_finished(result)
        diagnostics.finish(result.model_dump(mode="json"))
        return result
    except _InvalidTaskError as exc:
        result = build_failed_result(
            task_id=task_id,
            task_type=task_type,
            code="INVALID_TASK",
            message=str(exc),
            duration_ms=_elapsed_ms(started),
        )
        log_task_finished(result)
        diagnostics.finish(result.model_dump(mode="json"))
        return result

    if parsed.task_id != task_id:
        parsed = parsed.model_copy(update={"task_id": task_id})

    if runner is None:
        try:
            runner = create_runner(
                config=runtime_config,
                llm=llm,
                tools=tools,
                browser_factory=browser_factory,
                finder=finder,
                env_file=env_file,
            )
        except Exception as exc:
            result = build_failed_result(
                task_id=task_id,
                task_type=parsed.type,
                code="CONFIGURATION_ERROR",
                message=_exception_message(exc),
                duration_ms=_elapsed_ms(started),
            )
            log_task_finished(result)
            diagnostics.finish(result.model_dump(mode="json"))
            return result
    return await runner.run(parsed, emit_started=False, diagnostics=diagnostics)
