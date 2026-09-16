from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal

from browser_use import BrowserSession, Tools
from browser_use.llm.base import BaseChatModel
from pydantic import BaseModel, ConfigDict, Field

from job_page_finder.adapters.browser_use.gateway import BrowserUseFactory
from job_page_finder.adapters.diagnostics import FileDiagnosticsFactory
from job_page_finder.adapters.models.browser_use_chat import BrowserUseChatActionModel
from job_page_finder.application import TaskApplication
from job_page_finder.config import create_deepseek_llm
from job_page_finder.diagnostic_event_bridge import current_finder_event_sink
from job_page_finder.finder import JobPageFinder
from job_page_finder.ports import ActionModel, BrowserFactory, DiagnosticsFactory
from job_page_finder.runner import TaskRunner, request_identity
from job_page_finder.settings import (
    DEFAULT_DIAGNOSTICS_ROOT,
    BrowserSettings,
    DiagnosticsSettings,
    DiagnosticsStoragePolicy,
    FinderSettings,
    RuntimeSettings,
)
from job_page_finder.task_protocol import TaskRequest, TaskResult


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


class _RuntimeFinderProvider:
    def __init__(
        self,
        settings: RuntimeSettings,
        *,
        browser_factory: BrowserFactory | None = None,
        action_model: ActionModel | None = None,
        session_factory: Callable[[], BrowserSession] | None = None,
        tools: Tools | None = None,
        llm: BaseChatModel | None = None,
        finder: JobPageFinder | None = None,
        env_file: str | Path | None = None,
    ) -> None:
        self._settings = settings
        self._injected_browser_factory = browser_factory
        self._injected_action_model = action_model
        self._session_factory = session_factory
        self._tools = tools
        self._llm = llm
        self._finder = finder
        self._env_file = env_file

    def get(self) -> JobPageFinder:
        if self._finder is not None:
            return self._finder
        action_model = self._injected_action_model
        if action_model is None:
            llm = self._llm if self._llm is not None else create_deepseek_llm(env_file=self._env_file)
            action_model = BrowserUseChatActionModel(
                llm,
                max_dom_characters=self._settings.browser.max_dom_characters,
            )
        browser_factory = self._injected_browser_factory
        if browser_factory is None:
            browser_factory = BrowserUseFactory(
                browser_factory=self._session_factory,
                tools=self._tools,
                max_dom_characters=self._settings.browser.max_dom_characters,
                max_visual_candidates=self._settings.browser.max_visual_candidates,
                close_timeout=self._settings.finder.startup_timeout,
                scroll_route_timeout=self._settings.browser.scroll_route_timeout,
                scroll_route_poll_interval=self._settings.browser.scroll_route_poll_interval,
            )
        self._finder = JobPageFinder(
            browser_factory,
            action_model,
            settings=self._settings.finder,
            events_factory=current_finder_event_sink,
        )
        return self._finder


def settings_from_config(config: RuntimeConfig | None = None) -> RuntimeSettings:
    runtime_config = config or RuntimeConfig()
    return RuntimeSettings(
        finder=FinderSettings(
            step_timeout=runtime_config.step_timeout,
            startup_timeout=runtime_config.startup_timeout,
            max_consecutive_failures=runtime_config.max_consecutive_failures,
            use_vision=runtime_config.use_vision,
        ),
        browser=BrowserSettings(
            max_dom_characters=runtime_config.max_dom_characters,
            max_visual_candidates=runtime_config.max_visual_candidates,
        ),
        diagnostics=DiagnosticsSettings(
            root=runtime_config.diagnostics_root,
            storage=DiagnosticsStoragePolicy(
                max_runs=runtime_config.diagnostics_max_runs,
                retention_days=runtime_config.diagnostics_retention_days,
                max_run_bytes=runtime_config.diagnostics_max_run_bytes,
                max_total_bytes=runtime_config.diagnostics_max_total_bytes,
            ),
            level=runtime_config.diagnostics_level,
            capture_screenshots=runtime_config.diagnostics_screenshots,
        ),
    )


def _diagnostics_factory(settings: DiagnosticsSettings) -> DiagnosticsFactory:
    return FileDiagnosticsFactory(
        root=settings.root,
        level=settings.level,
        capture_screenshots=settings.capture_screenshots,
        max_runs=settings.storage.max_runs,
        retention_days=settings.storage.retention_days,
        max_run_bytes=settings.storage.max_run_bytes,
        max_total_bytes=settings.storage.max_total_bytes,
    )


def build_application(
    settings: RuntimeSettings | None = None,
    *,
    browser_factory: BrowserFactory | None = None,
    action_model: ActionModel | None = None,
    diagnostics_factory: DiagnosticsFactory | None = None,
    env_file: str | Path | None = None,
    session_factory: Callable[[], BrowserSession] | None = None,
    tools: Tools | None = None,
    llm: BaseChatModel | None = None,
    finder: JobPageFinder | None = None,
) -> TaskApplication:
    runtime_settings = settings or RuntimeSettings()
    provider = _RuntimeFinderProvider(
        runtime_settings,
        browser_factory=browser_factory,
        action_model=action_model,
        session_factory=session_factory,
        tools=tools,
        llm=llm,
        finder=finder,
        env_file=env_file,
    )
    return TaskApplication(
        provider,
        diagnostics_factory or _diagnostics_factory(runtime_settings.diagnostics),
    )


def create_runner(
    *,
    config: RuntimeConfig | None = None,
    llm: BaseChatModel | None = None,
    tools: Tools | None = None,
    browser_factory: Callable[[], BrowserSession] | None = None,
    finder: JobPageFinder | None = None,
    env_file: str | Path | None = None,
) -> TaskRunner:
    application = build_application(
        settings_from_config(config),
        session_factory=browser_factory,
        tools=tools,
        llm=llm,
        finder=finder,
        env_file=env_file,
    )
    return TaskRunner(application=application)


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
    if runner is not None:
        settings = settings_from_config(config)
        task_id, task_type = request_identity(request)
        diagnostics = _diagnostics_factory(settings.diagnostics).create(task_id=task_id, task_type=task_type)
        return await runner.run(request, diagnostics=diagnostics)
    application = build_application(
        settings_from_config(config),
        session_factory=browser_factory,
        tools=tools,
        llm=llm,
        finder=finder,
        env_file=env_file,
    )
    return await application.run(request)
