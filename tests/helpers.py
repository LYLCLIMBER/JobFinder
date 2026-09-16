from collections.abc import Callable

from browser_use import BrowserSession, Tools
from browser_use.llm.base import BaseChatModel

from job_page_finder.adapters.browser_use.gateway import BrowserUseFactory
from job_page_finder.adapters.models.browser_use_chat import BrowserUseChatActionModel
from job_page_finder.diagnostic_event_bridge import current_finder_event_sink
from job_page_finder.finder import JobPageFinder
from job_page_finder.settings import FinderSettings


def assemble_finder(
    llm: BaseChatModel,
    *,
    browser_factory: Callable[[], BrowserSession] | None = None,
    tools: Tools | None = None,
    max_consecutive_failures: int = 2,
    step_timeout: float = 30,
    startup_timeout: float = 30,
    max_dom_characters: int = 40_000,
    use_vision: bool = True,
    max_visual_candidates: int = 20,
    scroll_route_timeout: float = 2.0,
    scroll_route_poll_interval: float = 0.25,
) -> JobPageFinder:
    return JobPageFinder(
        BrowserUseFactory(
            browser_factory=browser_factory,
            tools=tools,
            max_dom_characters=max_dom_characters,
            max_visual_candidates=max_visual_candidates,
            close_timeout=startup_timeout,
            scroll_route_timeout=scroll_route_timeout,
            scroll_route_poll_interval=scroll_route_poll_interval,
        ),
        BrowserUseChatActionModel(llm, max_dom_characters=max_dom_characters),
        settings=FinderSettings(
            step_timeout=step_timeout,
            startup_timeout=startup_timeout,
            max_consecutive_failures=max_consecutive_failures,
            use_vision=use_vision,
        ),
        events_factory=current_finder_event_sink,
    )
