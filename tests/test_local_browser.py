import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from browser_use import BrowserSession
from browser_use.browser.profile import BrowserProfile
from browser_use.llm.views import ChatInvokeCompletion

from job_page_finder import JobPageFinder, JobPageFinderInput, run_task
from job_page_finder.models import AgentDecision


class StaticSiteHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/scroll-snap":
            body = b"""<!doctype html><html><head><title>Scrollable careers</title>
            <style>
            html, body { margin: 0; height: 100%; overflow: hidden; }
            #jobs { height: 100vh; overflow-y: auto; scroll-snap-type: y mandatory; }
            section { height: 100vh; scroll-snap-align: start; display: grid; place-items: center; }
            </style></head><body><main id="jobs" aria-label="Job openings">
            <section><h1>Explore our teams</h1></section><section id="second-screen"></section>
            </main><script>
            const jobs = document.querySelector('#jobs');
            jobs.addEventListener('scroll', () => {
              if (jobs.scrollTop > 0) document.querySelector('#second-screen').innerHTML =
                '<article><h2>Principal Scroll Engineer</h2><p>Remote - Platform</p></article>';
            }, { once: true });
            </script></body></html>"""
            status = 200
        elif self.path == "/careers":
            body = b"""<!doctype html><html><head><title>Open positions</title></head>
			<body><h1>Open positions</h1><article><h2>Senior Backend Engineer</h2>
			<p>Remote - Engineering</p></article></body></html>"""
            status = 200
        else:
            body = b"""<!doctype html><html><head><title>Example Company</title></head>
			<body><h1>Example Company</h1><a href="/careers">Careers</a></body></html>"""
            status = 200

        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        pass


class LocalSiteLlm:
    model = "local-test"

    @property
    def provider(self) -> str:
        return "fake"

    @property
    def name(self) -> str:
        return self.model

    async def ainvoke(self, messages, output_format=None, **kwargs):
        browser_state = messages[-1].text
        if "Senior Backend Engineer" in browser_state:
            decision = {
                "type": "done",
                "job_title": "Senior Backend Engineer",
                "evidence": "Senior Backend Engineer Remote - Engineering",
            }
        else:
            content_before_careers = browser_state.split("Careers", maxsplit=1)[0]
            indexes = re.findall(r"\[(\d+)\]", content_before_careers)
            assert indexes
            decision = {"type": "click", "index": int(indexes[-1])}

        completion = AgentDecision.model_validate({"decision": decision})
        return ChatInvokeCompletion(completion=completion, usage=None)


class ScrollSnapLlm:
    model = "scroll-snap-test"

    def __init__(self, *, use_target_index: bool) -> None:
        self.use_target_index = use_target_index

    @property
    def provider(self) -> str:
        return "fake"

    @property
    def name(self) -> str:
        return self.model

    async def ainvoke(self, messages, output_format=None, **kwargs):
        browser_state = messages[-1].text
        if "Principal Scroll Engineer" in browser_state:
            decision = {
                "type": "done",
                "job_title": "Principal Scroll Engineer",
                "evidence": "Principal Scroll Engineer Remote - Platform",
            }
        else:
            targets = browser_state.split("<scroll_targets>", maxsplit=1)[1].split("</scroll_targets>", maxsplit=1)[0]
            indexes = [int(index) for index in re.findall(r"\[(\d+)\].*down \d", targets) if int(index) > 0]
            assert indexes
            decision = {"type": "scroll", "direction": "down"}
            if self.use_target_index:
                decision["index"] = indexes[0]

        completion = AgentDecision.model_validate({"decision": decision})
        return ChatInvokeCompletion(completion=completion, usage=None)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_company_home_to_job_page_with_local_chromium() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), StaticSiteHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    def create_browser() -> BrowserSession:
        return BrowserSession(
            browser_profile=BrowserProfile(
                headless=True,
                user_data_dir=None,
                accept_downloads=False,
                auto_download_pdfs=False,
                highlight_elements=False,
                dom_highlight_elements=False,
                enable_default_extensions=False,
                block_ip_addresses=False,
            )
        )

    try:
        finder = JobPageFinder(llm=LocalSiteLlm(), browser_factory=create_browser)
        result = await finder.find(JobPageFinderInput(company_url=f"http://127.0.0.1:{port}", max_steps=3))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert result.success is True
    assert result.job_title == "Senior Backend Engineer"
    assert result.job_page_url == f"http://127.0.0.1:{port}/careers"
    assert result.steps == 2


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("use_target_index", [True, False], ids=["explicit-target", "root-fallback"])
async def test_internal_css_scroll_snap_reveals_job_with_local_chromium(use_target_index: bool) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), StaticSiteHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    def create_browser() -> BrowserSession:
        return BrowserSession(
            browser_profile=BrowserProfile(
                headless=True,
                user_data_dir=None,
                accept_downloads=False,
                auto_download_pdfs=False,
                highlight_elements=False,
                dom_highlight_elements=False,
                enable_default_extensions=False,
                block_ip_addresses=False,
            )
        )

    try:
        finder = JobPageFinder(llm=ScrollSnapLlm(use_target_index=use_target_index), browser_factory=create_browser)
        result = await finder.find(JobPageFinderInput(company_url=f"http://127.0.0.1:{port}/scroll-snap", max_steps=3))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert result.success is True
    assert result.job_title == "Principal Scroll Engineer"
    assert result.steps == 2


@pytest.mark.integration
@pytest.mark.asyncio
async def test_unified_runner_company_home_to_job_page_with_local_chromium() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), StaticSiteHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    def create_browser() -> BrowserSession:
        return BrowserSession(
            browser_profile=BrowserProfile(
                headless=True,
                user_data_dir=None,
                accept_downloads=False,
                auto_download_pdfs=False,
                highlight_elements=False,
                dom_highlight_elements=False,
                enable_default_extensions=False,
                block_ip_addresses=False,
            )
        )

    try:
        result = await run_task(
            {
                "version": "v1",
                "task_id": "local-chromium",
                "type": "find_job_page",
                "payload": {"company_url": f"http://127.0.0.1:{port}", "max_steps": 3},
            },
            llm=LocalSiteLlm(),
            browser_factory=create_browser,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert result.status == "succeeded"
    assert result.task_id == "local-chromium"
    assert result.output is not None
    assert result.output.job_title == "Senior Backend Engineer"
    assert result.output.job_page_url == f"http://127.0.0.1:{port}/careers"
    assert result.output.steps == 2
    assert result.error is None
    assert result.metadata.duration_ms >= 0
