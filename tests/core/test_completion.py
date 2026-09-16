from job_page_finder.completion import validate_completion
from job_page_finder.contracts import FindJobPageFailure, FindJobPageSuccess
from job_page_finder.core_models import Complete, PageObservation


def observation(**kwargs: object) -> PageObservation:
    values = {
        "url": "https://example.com/jobs",
        "title": "Jobs",
        "visible_text": "<div>Senior Backend Engineer</div>",
        "elements": (),
        "scroll_targets": (),
        "screenshot": None,
        "visual_candidates": (),
    }
    values.update(kwargs)
    return PageObservation(**values)  # type: ignore[arg-type]


def test_accepts_title_in_visible_text_without_requiring_evidence_in_dom() -> None:
    result = validate_completion(
        Complete(job_title="Senior Backend Engineer", evidence_quote="A quote that is not on the page"),
        observation(),
        step=2,
    )

    assert isinstance(result, FindJobPageSuccess)
    assert result.job_title == "Senior Backend Engineer"
    assert result.evidence.quote == "A quote that is not on the page"
    assert result.steps == 2


def test_accepts_generic_title_when_it_occurs_in_the_dom() -> None:
    result = validate_completion(
        Complete(job_title="Careers", evidence_quote="Careers"),
        observation(visible_text="<h1>Careers</h1>"),
        step=1,
    )

    assert isinstance(result, FindJobPageSuccess)
    assert result.job_title == "Careers"


def test_rejects_title_missing_from_visible_text() -> None:
    result = validate_completion(
        Complete(job_title="Chief Astronaut", evidence_quote="Chief Astronaut"),
        observation(),
        step=1,
    )

    assert isinstance(result, FindJobPageFailure)
    assert result.code == "VALIDATION_FAILED"
    assert result.retryable is False
    assert result.message == "job_title does not occur in the current visible DOM"


def test_rejects_whitespace_only_evidence_and_unusable_or_non_http_pages() -> None:
    empty_evidence = validate_completion(
        Complete(job_title="Senior Backend Engineer", evidence_quote="   "),
        observation(),
        step=1,
    )
    unavailable = validate_completion(
        Complete(job_title="Senior Backend Engineer", evidence_quote="Senior Backend Engineer"),
        observation(error="crashed"),
        step=1,
    )
    non_http = validate_completion(
        Complete(job_title="Senior Backend Engineer", evidence_quote="Senior Backend Engineer"),
        observation(url="about:blank"),
        step=1,
    )
    unusable = validate_completion(
        Complete(job_title="Senior Backend Engineer", evidence_quote="Senior Backend Engineer"),
        observation(is_usable=False),
        step=1,
    )

    assert all(isinstance(result, FindJobPageFailure) for result in (empty_evidence, unavailable, non_http, unusable))
    assert empty_evidence.message == "evidence is empty"
    assert unavailable.message == "browser state is unavailable: crashed"
    assert non_http.message == "current page is not an HTTP page"
    assert unusable.message == "current page has no usable DOM"
