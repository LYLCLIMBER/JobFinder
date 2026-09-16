from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

DEFAULT_DIAGNOSTICS_ROOT = Path("log/diagnostics")


class FinderSettings(BaseModel):
    step_timeout: float = Field(default=30, gt=0)
    startup_timeout: float = Field(default=30, gt=0)
    max_consecutive_failures: int = Field(default=2, ge=1)
    use_vision: bool = True


class BrowserSettings(BaseModel):
    max_dom_characters: int = Field(default=40_000, ge=1)
    max_visual_candidates: int = Field(default=20, ge=1)
    scroll_route_timeout: float = Field(default=2.0, gt=0)
    scroll_route_poll_interval: float = Field(default=0.25, gt=0)


class DiagnosticsStoragePolicy(BaseModel):
    max_runs: int = Field(default=100, ge=1)
    retention_days: int = Field(default=7, ge=0)
    max_run_bytes: int = Field(default=256 * 1024 * 1024, ge=1)
    max_total_bytes: int = Field(default=5 * 1024 * 1024 * 1024, ge=1)


class DiagnosticsSettings(BaseModel):
    root: Path = Field(default_factory=lambda: DEFAULT_DIAGNOSTICS_ROOT)
    storage: DiagnosticsStoragePolicy = Field(default_factory=DiagnosticsStoragePolicy)
    level: Literal["basic", "diagnostic", "raw"] = "basic"
    capture_screenshots: bool | None = None


class RuntimeSettings(BaseModel):
    finder: FinderSettings = Field(default_factory=FinderSettings)
    browser: BrowserSettings = Field(default_factory=BrowserSettings)
    diagnostics: DiagnosticsSettings = Field(default_factory=DiagnosticsSettings)
