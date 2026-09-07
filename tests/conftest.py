from pathlib import Path

import pytest

from job_page_finder import runtime


@pytest.fixture(autouse=True)
def isolate_default_diagnostics_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep implicit runtime and CLI diagnostics under each test's tmp_path."""
    root = tmp_path / "diagnostics"
    monkeypatch.setattr(runtime, "DEFAULT_DIAGNOSTICS_ROOT", root)
    return root
