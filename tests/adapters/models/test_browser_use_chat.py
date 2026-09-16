from typing import Any

import pytest
from pydantic import ValidationError

from job_page_finder.adapters.models.browser_use_chat import (
    AgentDecision,
    BrowserUseChatActionModel,
    action_from_decision,
)
from job_page_finder.core_models import (
    Click,
    Complete,
    DecisionContext,
    ElementRef,
    InteractiveElement,
    PageObservation,
    Scroll,
    ScrollTarget,
    VisualCandidate,
    Wait,
)


class FakeLlm:
    def __init__(self, decisions: list[dict[str, Any] | Exception]) -> None:
        self.decisions = decisions
        self.calls: list[list[Any]] = []

    async def ainvoke(self, messages, output_format=None, **kwargs):
        self.calls.append(messages)
        decision = self.decisions.pop(0)
        if isinstance(decision, Exception):
            raise decision
        completion = AgentDecision.model_validate({"decision": decision})
        assert output_format is AgentDecision
        return type("Response", (), {"completion": completion, "usage": None})()


def observation(**kwargs: object) -> PageObservation:
    values = {
        "url": "https://example.com/jobs",
        "title": "Example Company",
        "visible_text": "[7]<a>Careers</a>",
        "elements": (
            InteractiveElement(ref=ElementRef("observation:1:index:7"), text="Careers", role=None, href="/careers"),
        ),
        "scroll_targets": (
            ScrollTarget(ref=None, description="Root page", can_scroll_up=False, can_scroll_down=True),
            ScrollTarget(
                ref=ElementRef("observation:1:index:9"),
                description="Job list",
                can_scroll_up=False,
                can_scroll_down=True,
            ),
        ),
        "screenshot": None,
        "visual_candidates": (),
    }
    values.update(kwargs)
    return PageObservation(**values)  # type: ignore[arg-type]


def context(current: PageObservation | None = None, **kwargs: object) -> DecisionContext:
    values = {
        "observation": current or observation(),
        "previous_outcome": "No previous action.",
        "step": 1,
        "max_steps": 8,
        "company_url": "https://example.com",
    }
    values.update(kwargs)
    return DecisionContext(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"type": "click", "index": 0}, "greater than or equal to 1"),
        ({"type": "wait", "seconds": 0}, "greater than or equal to 1"),
        ({"type": "wait", "seconds": 6}, "less than or equal to 5"),
        ({"type": "scroll", "direction": "sideways"}, "up"),
        ({"type": "click", "index": 1, "extra": True}, "Extra inputs are not permitted"),
        ({"type": "navigate", "url": "https://evil.example"}, "input_type"),
        ({"type": "done", "job_title": "Engineer"}, "evidence"),
    ],
)
def test_action_schema_rejects_unknown_fields_and_out_of_range_values(raw: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        AgentDecision.model_validate({"decision": raw})


def test_action_schema_accepts_the_four_allowed_actions() -> None:
    scroll = AgentDecision.model_validate({"decision": {"type": "scroll", "direction": "down"}})
    wait = AgentDecision.model_validate({"decision": {"type": "wait", "seconds": 2}})
    done = AgentDecision.model_validate({"decision": {"type": "done", "job_title": "Engineer", "evidence": "Engineer"}})

    assert action_from_decision(
        AgentDecision.model_validate({"decision": {"type": "click", "index": 1}}).decision, observation()
    ) == Click(element=ElementRef("observation:1:index:7"))
    assert action_from_decision(scroll.decision, observation()) == Scroll(direction="down", target=None)
    assert action_from_decision(wait.decision, observation()) == Wait(seconds=2)
    assert action_from_decision(done.decision, observation()) == Complete(
        job_title="Engineer", evidence_quote="Engineer"
    )


def test_model_indexes_resolve_against_the_current_observation_only() -> None:
    current = observation()
    previous = observation(
        elements=(InteractiveElement(ref=ElementRef("observation:0:index:7"), text="Old", role=None, href=None),),
        scroll_targets=(),
    )
    click = action_from_decision(
        AgentDecision.model_validate({"decision": {"type": "click", "index": 1}}).decision,
        current,
    )
    stale = action_from_decision(
        AgentDecision.model_validate({"decision": {"type": "click", "index": 1}}).decision,
        observation(elements=(), scroll_targets=()),
    )
    scroll = action_from_decision(
        AgentDecision.model_validate({"decision": {"type": "scroll", "direction": "down", "index": 2}}).decision,
        current,
    )

    assert isinstance(click, Click) and click.element == ElementRef("observation:1:index:7")
    assert click.element != previous.elements[0].ref
    assert isinstance(stale, Click) and stale.element == ElementRef("unresolved:1")
    assert isinstance(scroll, Scroll) and scroll.target == ElementRef("observation:1:index:9")


@pytest.mark.asyncio
async def test_text_context_includes_stable_fields_and_no_conversation_history() -> None:
    llm = FakeLlm(
        [
            {"type": "wait", "seconds": 1},
            {"type": "done", "job_title": "Engineer", "evidence": "Engineer"},
        ]
    )
    model = BrowserUseChatActionModel(llm, max_dom_characters=40_000)

    first = await model.decide(context())
    second = await model.decide(context(previous_outcome="Waited for 1 seconds", step=2))

    assert isinstance(first, Wait)
    assert isinstance(second, Complete)
    assert len(llm.calls) == 2
    assert all(len(messages) == 2 for messages in llm.calls)
    prompt = llm.calls[0][-1].text
    assert "Original company URL: https://example.com" in prompt
    assert "Current URL: https://example.com/jobs" in prompt
    assert "Page title: Example Company" in prompt
    assert "[7]<a>Careers</a>" in prompt
    assert "[1] Careers" in prompt
    assert "[2] Job list" in prompt
    assert "image_url" not in str(llm.calls[0])
    assert "Waited for 1 seconds" in llm.calls[1][-1].text


@pytest.mark.asyncio
async def test_visual_messages_include_annotated_indexes_only_when_candidates_exist() -> None:
    visual = observation(
        screenshot=b"png-bytes",
        visual_candidates=(VisualCandidate(ref=ElementRef("observation:1:index:7"), box=(1, 2, 3, 4)),),
    )
    llm = FakeLlm([{"type": "wait", "seconds": 1}, {"type": "wait", "seconds": 1}])
    model = BrowserUseChatActionModel(llm, max_dom_characters=40_000)

    await model.decide(context(visual))
    await model.decide(context(observation(screenshot=b"png-bytes", visual_candidates=())))

    visual_parts = llm.calls[0][-1].content
    assert any("[1]" in getattr(part, "text", "") for part in visual_parts)
    assert any(getattr(part, "type", None) == "image_url" for part in visual_parts)
    assert isinstance(llm.calls[1][-1].text, str)


def test_opaque_element_refs_use_local_display_indexes() -> None:
    current = observation(
        elements=(InteractiveElement(ref=ElementRef("opaque-link"), text="Careers", role=None, href=None),),
        scroll_targets=(
            ScrollTarget(ref=None, description="Root page", can_scroll_up=False, can_scroll_down=True),
            ScrollTarget(
                ref=ElementRef("opaque-scroll"), description="Job list", can_scroll_up=False, can_scroll_down=True
            ),
        ),
        visual_candidates=(VisualCandidate(ref=ElementRef("opaque-link"), box=(1, 2, 3, 4)),),
    )

    click = action_from_decision(
        AgentDecision.model_validate({"decision": {"type": "click", "index": 1}}).decision,
        current,
    )
    scroll = action_from_decision(
        AgentDecision.model_validate({"decision": {"type": "scroll", "direction": "down", "index": 2}}).decision,
        current,
    )

    assert click == Click(element=ElementRef("opaque-link"))
    assert scroll == Scroll(direction="down", target=ElementRef("opaque-scroll"))
