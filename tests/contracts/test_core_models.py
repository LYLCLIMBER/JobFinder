import pytest

from job_page_finder.core_models import Complete, ElementRef, Scroll, Wait


def test_core_actions_keep_the_existing_action_constraints() -> None:
    assert Scroll(direction="down", target=ElementRef("index:7")).target == ElementRef("index:7")
    assert Wait(seconds=5).seconds == 5
    assert Complete(job_title="Engineer", evidence_quote="Engineer").job_title == "Engineer"

    with pytest.raises(ValueError):
        Scroll(direction="sideways")
    with pytest.raises(ValueError):
        Wait(seconds=0)
    with pytest.raises(ValueError):
        Complete(job_title="", evidence_quote="evidence")
