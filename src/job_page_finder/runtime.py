import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from browser_use import BrowserSession, Tools
from browser_use.llm.base import BaseChatModel
from pydantic import BaseModel, ConfigDict, Field

from job_page_finder.config import create_deepseek_llm
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


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_timeout: float = Field(default=30, gt=0)
    startup_timeout: float = Field(default=30, gt=0)
    max_consecutive_failures: int = Field(default=2, ge=1)
    max_dom_characters: int = Field(default=40_000, ge=1)
    use_vision: bool = False
    max_visual_candidates: int = Field(default=20, ge=1)


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
        return result

    if parsed.task_id != task_id:
        parsed = parsed.model_copy(update={"task_id": task_id})

    if runner is None:
        try:
            runner = create_runner(
                config=config,
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
            return result
    return await runner.run(parsed, emit_started=False)
