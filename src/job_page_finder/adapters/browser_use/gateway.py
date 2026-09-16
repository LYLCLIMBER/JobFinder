from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any, Literal

from browser_use import BrowserSession, Tools
from browser_use.browser.events import BrowserStopEvent
from browser_use.browser.profile import BrowserProfile

from job_page_finder.adapters.browser_use.observation import ObservationConversion, build_observation
from job_page_finder.adapters.browser_use.scroll_targets import discover_scroll_targets, find_scroll_target
from job_page_finder.adapters.browser_use.scrolling import perform_scroll
from job_page_finder.core_models import ActionOutcome, ElementRef, ObservationOptions, PageObservation

logger = logging.getLogger(__name__)


class BrowserUseFactory:
    def __init__(
        self,
        *,
        browser_factory: Callable[[], BrowserSession] | None = None,
        tools: Tools | None = None,
        headless: bool = True,
        block_ip_addresses: bool = True,
        max_dom_characters: int = 40_000,
        max_visual_candidates: int = 20,
        close_timeout: float = 30,
        scroll_route_timeout: float = 2.0,
        scroll_route_poll_interval: float = 0.25,
    ) -> None:
        self._browser_factory = browser_factory or self.create_session
        self._tools = tools or Tools()
        self.headless = headless
        self.block_ip_addresses = block_ip_addresses
        self.max_dom_characters = max_dom_characters
        self.max_visual_candidates = max_visual_candidates
        self.close_timeout = close_timeout
        self.scroll_route_timeout = scroll_route_timeout
        self.scroll_route_poll_interval = scroll_route_poll_interval

    def create_session(self) -> BrowserSession:
        return BrowserSession(browser_profile=self.create_profile())

    def create_profile(self) -> BrowserProfile:
        return BrowserProfile(
            headless=self.headless,
            user_data_dir=None,
            accept_downloads=False,
            auto_download_pdfs=False,
            highlight_elements=False,
            dom_highlight_elements=False,
            enable_default_extensions=False,
            block_ip_addresses=self.block_ip_addresses,
        )

    async def open(self) -> BrowserUseSessionAdapter:
        session = self._browser_factory()
        adapter = BrowserUseSessionAdapter(
            session,
            tools=self._tools,
            max_dom_characters=self.max_dom_characters,
            max_visual_candidates=self.max_visual_candidates,
            close_timeout=self.close_timeout,
            scroll_route_timeout=self.scroll_route_timeout,
            scroll_route_poll_interval=self.scroll_route_poll_interval,
        )
        try:
            await session.start()
        except BaseException:
            await adapter.close()
            raise
        return adapter


class BrowserUseSessionAdapter:
    def __init__(
        self,
        session: BrowserSession,
        *,
        tools: Tools,
        max_dom_characters: int,
        max_visual_candidates: int,
        close_timeout: float,
        scroll_route_timeout: float,
        scroll_route_poll_interval: float,
    ) -> None:
        self._session = session
        self._tools = tools
        self._max_dom_characters = max_dom_characters
        self._max_visual_candidates = max_visual_candidates
        self._close_timeout = close_timeout
        self._scroll_route_timeout = scroll_route_timeout
        self._scroll_route_poll_interval = scroll_route_poll_interval
        self._conversion: ObservationConversion | None = None
        self._state: Any = None
        self._close_task: asyncio.Task[None] | None = None
        self._generation = 0

    async def navigate(self, url: str) -> None:
        await self._session.navigate_to(url)
        self._conversion = None
        self._state = None

    async def observe(self, options: ObservationOptions = ObservationOptions()) -> PageObservation:
        state = await self._session.get_browser_state_summary(include_screenshot=options.include_image)
        self._generation += 1
        conversion = build_observation(
            state,
            include_image=options.include_image,
            generation=self._generation,
            max_dom_characters=self._max_dom_characters,
            max_visual_candidates=self._max_visual_candidates,
        )
        self._state = state
        self._conversion = conversion
        return conversion.observation

    def element_ref_for_index(self, index: int) -> ElementRef | None:
        if self._conversion is None:
            return None
        return next(
            (ref for ref in self._conversion.element_map if _index_from_ref(ref) == index),
            None,
        )

    def scroll_ref_for_index(self, index: int) -> ElementRef | None:
        if self._conversion is None:
            return None
        return next(
            (ref for ref in self._conversion.scroll_index.bindings if _index_from_ref(ref) == index),
            None,
        )

    async def click(self, element: ElementRef) -> ActionOutcome:
        if self._conversion is None or element not in self._conversion.element_map:
            raise ValueError(f"Element reference {element.value} is not available in the current observation")
        index = _index_from_ref(element)
        observed_node = self._conversion.element_map[element]
        state = await self._session.get_browser_state_summary(include_screenshot=False)
        current_node = (getattr(getattr(state, "dom_state", None), "selector_map", {}) or {}).get(index)
        observed_backend_id = getattr(observed_node, "backend_node_id", None)
        current_backend_id = getattr(current_node, "backend_node_id", None)
        same_element = (
            current_node is observed_node
            if observed_backend_id is None or current_backend_id is None
            else observed_backend_id == current_backend_id
        )
        if not same_element:
            raise ValueError(f"Element reference {element.value} is stale")
        result = await self._tools.click(index=index, browser_session=self._session)
        if result.error:
            raise RuntimeError(result.error)
        return ActionOutcome(
            changed=True,
            current_url=str(await self._session.get_current_page_url()),
            description=result.extracted_content or "Action completed",
        )

    async def scroll(
        self,
        *,
        target: ElementRef | None,
        direction: Literal["up", "down"],
    ) -> ActionOutcome:
        if self._conversion is None or self._state is None:
            raise ValueError("A page observation is required before scrolling")
        state = await self._session.get_browser_state_summary(include_screenshot=False)
        current_targets = discover_scroll_targets(state)
        resolved = None
        if target is not None:
            if target not in self._conversion.scroll_index.bindings:
                raise ValueError(f"Scroll target {target.value} is not available in the current observation")
            resolved = find_scroll_target(state, self._conversion.scroll_index.bindings[target])
            if resolved is None or not resolved.can_scroll(direction):
                raise ValueError(f"Scroll target {target.value} is not available in the requested direction")
        execution = await perform_scroll(
            self._session,
            self._tools,
            state,
            target=resolved,
            direction=direction,
            available_targets=current_targets,
            route_timeout=self._scroll_route_timeout,
            route_poll_interval=self._scroll_route_poll_interval,
        )
        self._state = execution.state
        return execution.outcome

    async def wait(self, seconds: int) -> ActionOutcome:
        await asyncio.sleep(seconds)
        current_url = str(
            await asyncio.wait_for(
                self._session.get_current_page_url(),
                timeout=self._scroll_route_timeout,
            )
        )
        return ActionOutcome(
            changed=False,
            current_url=current_url,
            description=f"Waited for {seconds} seconds",
        )

    async def close(self) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._session.kill())
        finished, caller_cancelled = await _await_cleanup(self._close_task, self._close_timeout)
        cleanup_failed = finished and (self._close_task.cancelled() or self._close_task.exception() is not None)
        if not finished or cleanup_failed:
            logger.warning("Browser cleanup failed, timed out, or cancelled itself")
            self._close_task.cancel()
            force_task = asyncio.create_task(self._force_terminate())
            force_finished, force_cancelled = await _await_cleanup(force_task, self._close_timeout)
            caller_cancelled = caller_cancelled or force_cancelled
            if force_cancelled and not force_finished:
                force_finished, _ = await _await_cleanup(force_task, self._close_timeout)
            if not force_finished:
                force_task.cancel()
        if caller_cancelled:
            raise asyncio.CancelledError

    async def _force_terminate(self) -> None:
        event_bus = getattr(self._session, "event_bus", None)
        if event_bus is None:
            return
        try:
            event = event_bus.dispatch(BrowserStopEvent(force=True))
            await asyncio.wait_for(event, timeout=self._close_timeout)
            await asyncio.wait_for(
                event.event_result(raise_if_any=True, raise_if_none=False),
                timeout=self._close_timeout,
            )
        except BaseException:
            pass


async def _await_cleanup(task: asyncio.Task[None], timeout: float) -> tuple[bool, bool]:
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        return True, False
    except asyncio.CancelledError:
        current = asyncio.current_task()
        return task.done(), bool(current and current.cancelling())
    except BaseException:
        return task.done(), False


def _index_from_ref(ref: ElementRef) -> int:
    try:
        return int(ref.value.rsplit(":", maxsplit=1)[-1])
    except ValueError as exc:
        raise ValueError(f"Invalid browser-use element reference: {ref.value}") from exc
