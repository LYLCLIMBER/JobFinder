import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from browser_use import BrowserSession
from browser_use.browser.profile import BrowserProfile
from browser_use.llm.views import ChatInvokeCompletion

from job_page_finder import JobPageFinder, JobPageFinderInput
from job_page_finder.models import AgentDecision


class StaticSiteHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/careers":
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
