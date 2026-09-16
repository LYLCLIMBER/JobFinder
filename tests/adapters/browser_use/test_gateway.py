import asyncio
import base64
import io
from types import SimpleNamespace

import pytest
from PIL import Image

from job_page_finder.adapters.browser_use import gateway
from job_page_finder.adapters.browser_use.gateway import BrowserUseFactory
from job_page_finder.core_models import ElementRef, ObservationOptions


class FakeSession:
    def __init__(self, state=None, *, start_error: BaseException | None = None, kill: object = None) -> None:
        self.state = state
        self.start_error = start_error
        self.kill_behavior = kill
        self.started = False
        self.kill_calls = 0
        self.navigated_to = None
        self.kill_started = asyncio.Event()
        self.kill_release = asyncio.Event()
        self.event_bus = FakeEventBus(self.kill_release)

    async def start(self) -> None:
        if self.start_error is not None:
            raise self.start_error
        self.started = True

    async def navigate_to(self, url: str) -> None:
        self.navigated_to = url

    async def get_browser_state_summary(self, *, include_screenshot: bool):
        return self.state

    async def get_current_page_url(self) -> str:
        return self.state.url

    async def kill(self) -> None:
        self.kill_calls += 1
        self.kill_started.set()
        if self.kill_behavior == "block":
            await self.kill_release.wait()
        elif self.kill_behavior == "resist":
            while not self.kill_release.is_set():
                try:
                    await self.kill_release.wait()
                except asyncio.CancelledError:
                    continue
        elif isinstance(self.kill_behavior, BaseException):
            raise self.kill_behavior


class FakeStopEvent:
    def __await__(self):
        return asyncio.sleep(0).__await__()

    async def event_result(self, **kwargs) -> None:
        return None


class FakeEventBus:
    def __init__(self, release: asyncio.Event) -> None:
        self.release = release
        self.force_stops = 0

    def dispatch(self, event) -> FakeStopEvent:
        if getattr(event, "force", False):
            self.force_stops += 1
            self.release.set()
        return FakeStopEvent()


class BlockingForceEvent(FakeStopEvent):
    def __init__(self, release: asyncio.Event) -> None:
        self.release = release

    def __await__(self):
        return self.release.wait().__await__()


class BlockingForceEventBus(FakeEventBus):
    def __init__(self) -> None:
        super().__init__(asyncio.Event())
        self.started = asyncio.Event()
        self.force_release = asyncio.Event()

    def dispatch(self, event) -> BlockingForceEvent:
        self.force_stops += 1
        self.started.set()
        return BlockingForceEvent(self.force_release)


class FakeTools:
    def __init__(self) -> None:
        self.clicked = []

    async def click(self, *, index: int, browser_session: FakeSession):
        self.clicked.append((index, browser_session))
        return SimpleNamespace(error=None, extracted_content=f"Clicked element {index}")


def browser_state():
    image = Image.new("RGB", (200, 100), "white")
    output = io.BytesIO()
    image.save(output, format="PNG")
    node = SimpleNamespace(
        absolute_position=SimpleNamespace(x=20, y=20, width=100, height=50),
        is_visible=True,
        ax_node=None,
        children=[],
        attributes={"role": "button", "href": "/jobs"},
        get_meaningful_text_for_llm=lambda: "",
    )
    dom = SimpleNamespace(
        selector_map={18: node},
        _root=None,
        llm_representation=lambda: "[18]<button></button>",
    )
    return SimpleNamespace(
        url="https://example.com/",
        title="Example",
        dom_state=dom,
        screenshot=base64.b64encode(output.getvalue()).decode("ascii"),
        page_info=SimpleNamespace(
            viewport_width=200,
            viewport_height=100,
            scroll_x=0,
            scroll_y=0,
            pixels_above=0,
            pixels_below=0,
        ),
    )


@pytest.mark.asyncio
async def test_factory_and_session_implement_browser_port() -> None:
    session = FakeSession(browser_state())
    tools = FakeTools()
    adapter = await BrowserUseFactory(browser_factory=lambda: session, tools=tools).open()

    await adapter.navigate("https://example.com")
    observation = await adapter.observe(ObservationOptions(include_image=True))
    outcome = await adapter.click(observation.elements[0].ref)
    await adapter.close()
    await adapter.close()

    assert session.started is True
    assert session.navigated_to == "https://example.com"
    assert observation.visible_text == "[18]<button></button>"
    assert observation.screenshot is not None and observation.screenshot.startswith(b"\x89PNG")
    assert observation.visual_candidates[0].ref == ElementRef("observation:1:index:18")
    assert outcome.description == "Clicked element 18"
    assert session.kill_calls == 1


@pytest.mark.asyncio
async def test_click_rejects_reference_when_selector_now_points_to_another_node() -> None:
    session = FakeSession(browser_state())
    tools = FakeTools()
    adapter = await BrowserUseFactory(browser_factory=lambda: session, tools=tools).open()
    old_observation = await adapter.observe()
    session.state = browser_state()
    await adapter.observe()

    with pytest.raises(ValueError, match="not available"):
        await adapter.click(old_observation.elements[0].ref)

    assert tools.clicked == []
    await adapter.close()


def test_factory_profile_preserves_browser_safety_defaults(monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr(gateway, "BrowserProfile", lambda **kwargs: captured.update(kwargs) or kwargs)

    profile = BrowserUseFactory().create_profile()

    assert profile == captured
    assert captured == {
        "headless": True,
        "user_data_dir": None,
        "accept_downloads": False,
        "auto_download_pdfs": False,
        "highlight_elements": False,
        "dom_highlight_elements": False,
        "enable_default_extensions": False,
        "block_ip_addresses": True,
    }


@pytest.mark.asyncio
async def test_open_failure_cleans_partial_session_without_masking_error() -> None:
    session = FakeSession(start_error=RuntimeError("start failed"), kill=RuntimeError("kill failed"))

    with pytest.raises(RuntimeError, match="start failed"):
        await BrowserUseFactory(browser_factory=lambda: session, tools=FakeTools()).open()

    assert session.kill_calls == 1


@pytest.mark.asyncio
async def test_close_is_bounded_and_distinguishes_cancellation_sources() -> None:
    self_cancelled = FakeSession(browser_state(), kill=asyncio.CancelledError())
    adapter = await BrowserUseFactory(browser_factory=lambda: self_cancelled, tools=FakeTools()).open()
    await adapter.close()
    assert self_cancelled.event_bus.force_stops == 1

    blocked = FakeSession(browser_state(), kill="block")
    adapter = await BrowserUseFactory(
        browser_factory=lambda: blocked,
        tools=FakeTools(),
        close_timeout=0.01,
    ).open()
    await adapter.close()
    assert blocked.kill_calls == 1

    caller_cancelled = FakeSession(browser_state(), kill="block")
    adapter = await BrowserUseFactory(
        browser_factory=lambda: caller_cancelled,
        tools=FakeTools(),
        close_timeout=1,
    ).open()
    task = asyncio.create_task(adapter.close())
    await caller_cancelled.kill_started.wait()
    task.cancel()
    caller_cancelled.kill_release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert caller_cancelled.kill_calls == 1

    resistant = FakeSession(browser_state(), kill="resist")
    adapter = await BrowserUseFactory(
        browser_factory=lambda: resistant,
        tools=FakeTools(),
        close_timeout=0.01,
    ).open()
    await adapter.close()
    await asyncio.sleep(0)
    assert resistant.event_bus.force_stops == 1


@pytest.mark.asyncio
async def test_caller_cancellation_during_force_cleanup_is_propagated() -> None:
    session = FakeSession(browser_state(), kill=RuntimeError("kill failed"))
    session.event_bus = BlockingForceEventBus()
    adapter = await BrowserUseFactory(
        browser_factory=lambda: session,
        tools=FakeTools(),
        close_timeout=1,
    ).open()

    task = asyncio.create_task(adapter.close())
    await session.event_bus.started.wait()
    task.cancel()
    session.event_bus.force_release.set()

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_wait_reports_the_live_url() -> None:
    session = FakeSession(browser_state())
    adapter = await BrowserUseFactory(browser_factory=lambda: session, tools=FakeTools()).open()
    await adapter.observe()
    session.state.url = "https://example.com/jobs"

    outcome = await adapter.wait(0)

    assert outcome.current_url == "https://example.com/jobs"
    await adapter.close()
