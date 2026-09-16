from dataclasses import dataclass
from typing import Literal, TypeAlias


@dataclass(frozen=True)
class ElementRef:
    value: str

    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("element reference must not be empty")


@dataclass(frozen=True)
class InteractiveElement:
    ref: ElementRef
    text: str
    role: str | None
    href: str | None


@dataclass(frozen=True)
class ScrollTarget:
    ref: ElementRef | None
    description: str
    can_scroll_up: bool
    can_scroll_down: bool


@dataclass(frozen=True)
class VisualCandidate:
    ref: ElementRef
    box: tuple[int, int, int, int]


@dataclass(frozen=True)
class PageObservation:
    url: str
    title: str
    visible_text: str
    elements: tuple[InteractiveElement, ...]
    scroll_targets: tuple[ScrollTarget, ...]
    screenshot: bytes | None
    visual_candidates: tuple[VisualCandidate, ...]
    is_usable: bool = True
    error: str | None = None


@dataclass(frozen=True)
class Click:
    element: ElementRef


@dataclass(frozen=True)
class Scroll:
    direction: Literal["up", "down"]
    target: ElementRef | None = None

    def __post_init__(self) -> None:
        if self.direction not in {"up", "down"}:
            raise ValueError("scroll direction must be up or down")


@dataclass(frozen=True)
class Wait:
    seconds: int

    def __post_init__(self) -> None:
        if not 1 <= self.seconds <= 5:
            raise ValueError("wait seconds must be between 1 and 5")


@dataclass(frozen=True)
class Complete:
    job_title: str
    evidence_quote: str

    def __post_init__(self) -> None:
        if not self.job_title or not self.evidence_quote:
            raise ValueError("completion requires a job title and evidence quote")


AgentAction: TypeAlias = Click | Scroll | Wait | Complete


@dataclass(frozen=True)
class DecisionContext:
    observation: PageObservation
    previous_outcome: str
    step: int
    max_steps: int
    company_url: str

    def __post_init__(self) -> None:
        if self.step < 1 or self.max_steps < 1 or self.step > self.max_steps:
            raise ValueError("step must be within the configured step budget")
        if not self.company_url:
            raise ValueError("company_url must not be empty")


@dataclass(frozen=True)
class ActionOutcome:
    changed: bool
    current_url: str
    description: str


@dataclass(frozen=True)
class ObservationOptions:
    include_image: bool = False
