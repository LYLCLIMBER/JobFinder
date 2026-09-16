from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Literal

from browser_use.browser.events import ScrollEvent

from job_page_finder.adapters.browser_use.scroll_targets import (
    ScrollTarget,
    find_scroll_target,
    root_scroll_target,
    targets_for_direction,
)
from job_page_finder.core_models import ActionOutcome


@dataclass(frozen=True)
class ScrollExecution:
    outcome: ActionOutcome
    state: Any


async def perform_scroll(
    browser: Any,
    tools: Any,
    state: Any,
    *,
    target: ScrollTarget | None,
    direction: Literal["up", "down"],
    available_targets: tuple[ScrollTarget, ...],
    route_timeout: float,
    route_poll_interval: float,
) -> ScrollExecution:
    attempts = (
        [root_scroll_target(state), *targets_for_direction(available_targets, direction)]
        if target is None
        else [target]
    )
    current_state = state
    for original_target in attempts:
        current_target = find_scroll_target(current_state, original_target)
        if current_target is None or (current_target.index != 0 and not current_target.can_scroll(direction)):
            continue
        before_url = str(await browser.get_current_page_url()) if original_target.index == 0 else None
        await _dispatch_scroll(browser, tools, current_state, current_target, direction)
        current_state = await browser.get_browser_state_summary(include_screenshot=False)
        for observed_target in attempts:
            updated = find_scroll_target(current_state, observed_target)
            if updated is None or updated.offset == observed_target.offset:
                continue
            distance = abs(updated.offset - observed_target.offset)
            label = "root page" if observed_target.index == 0 else f"element [{observed_target.index}]"
            return ScrollExecution(
                outcome=ActionOutcome(
                    changed=True,
                    current_url=str(current_state.url),
                    description=f"Scrolled {label} {direction} by {distance:g}px",
                ),
                state=current_state,
            )
        if before_url is not None:
            changed_url = await _wait_for_route_change(browser, before_url, route_timeout, route_poll_interval)
            if changed_url:
                return ScrollExecution(
                    outcome=ActionOutcome(
                        changed=True,
                        current_url=changed_url,
                        description=f"Wheel gesture changed route to {changed_url}",
                    ),
                    state=current_state,
                )
    raise RuntimeError("Scroll had no effect on the root page or available scroll targets")


async def _dispatch_scroll(
    browser: Any,
    tools: Any,
    state: Any,
    target: ScrollTarget,
    direction: Literal["up", "down"],
) -> None:
    selector_node = getattr(getattr(state, "dom_state", None), "selector_map", {}).get(target.index)
    use_registered_target = (
        target.index != 0
        and selector_node is not None
        and getattr(selector_node, "backend_node_id", None) == target.backend_node_id
    )
    if target.index == 0 or use_registered_target:
        result = await tools.scroll(
            down=direction == "down",
            pages=1.0,
            index=target.index if use_registered_target else None,
            browser_session=browser,
        )
        if result.error:
            raise RuntimeError(result.error)
        return
    amount = int(getattr(getattr(state, "page_info", None), "viewport_height", 1000) or 1000)
    event = browser.event_bus.dispatch(ScrollEvent(direction=direction, amount=amount, node=target.node))
    await event
    await event.event_result(raise_if_any=True, raise_if_none=False)


async def _wait_for_route_change(browser: Any, before_url: str, timeout: float, poll_interval: float) -> str | None:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        remaining = end - loop.time()
        try:
            current_url = str(await asyncio.wait_for(browser.get_current_page_url(), timeout=remaining))
        except TimeoutError:
            return None
        if current_url != before_url:
            return current_url
        await asyncio.sleep(min(poll_interval, max(0, end - loop.time())))
    return None
