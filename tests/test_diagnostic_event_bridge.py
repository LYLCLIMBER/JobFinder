from typing import Any

from job_page_finder.core_models import ElementRef, PageObservation, VisualCandidate
from job_page_finder.diagnostic_event_bridge import FileFinderEventSink


class RecordingDiagnostics:
    capture_screenshots = False
    is_diagnostic = True
    is_raw = False

    def __init__(self) -> None:
        self.artifacts: list[tuple[str, Any]] = []

    def event(self, *args: object, **kwargs: object) -> None:
        pass

    def artifact(self, name: str, value: Any, **kwargs: object) -> None:
        self.artifacts.append((name, value))

    def screenshot(self, *args: object, **kwargs: object) -> None:
        pass


def test_visual_candidate_diagnostics_preserve_opaque_refs() -> None:
    diagnostics = RecordingDiagnostics()
    sink = FileFinderEventSink(diagnostics)
    observation = PageObservation(
        url="https://example.com",
        title="Example",
        visible_text="Jobs",
        elements=(),
        scroll_targets=(),
        screenshot=b"png",
        visual_candidates=(VisualCandidate(ref=ElementRef("opaque-candidate"), box=(1, 2, 3, 4)),),
    )

    sink.page_observed(1, observation)

    assert ("visual_candidates", [{"ref": "opaque-candidate", "coordinates": [1, 2, 3, 4]}]) in diagnostics.artifacts
