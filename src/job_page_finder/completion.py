import html
import re

from pydantic import ValidationError

from job_page_finder.contracts import (
    RETRYABLE_FINDER_FAILURE_CODES,
    FindJobPageFailure,
    FindJobPageResult,
    FindJobPageSuccess,
    JobEvidence,
)
from job_page_finder.core_models import Complete, PageObservation


def validate_completion(
    action: Complete,
    observation: PageObservation,
    *,
    step: int,
) -> FindJobPageResult:
    job_title = _normalize_text(action.job_title)
    evidence = _normalize_text(action.evidence_quote)
    visible_text = _normalize_text(observation.visible_text, remove_markup=True)

    if not job_title:
        return _failure("job_title is empty", step)
    if not evidence:
        return _failure("evidence is empty", step)
    if observation.error:
        return _failure(f"browser state is unavailable: {observation.error}", step)
    if not observation.url.lower().startswith(("http://", "https://")):
        return _failure("current page is not an HTTP page", step)
    if not observation.is_usable:
        return _failure("current page has no usable DOM", step)
    if job_title not in visible_text:
        return _failure("job_title does not occur in the current visible DOM", step)

    try:
        return FindJobPageSuccess(
            status="succeeded",
            job_page_url=observation.url,
            job_title=action.job_title.strip(),
            evidence=JobEvidence(quote=action.evidence_quote.strip(), source_url=observation.url),
            steps=step,
        )
    except ValidationError:
        return _failure("current page is not an HTTP page", step)


def _failure(message: str, step: int) -> FindJobPageFailure:
    return FindJobPageFailure(
        status="failed",
        code="VALIDATION_FAILED",
        message=message,
        retryable="VALIDATION_FAILED" in RETRYABLE_FINDER_FAILURE_CODES,
        steps=step,
    )


def _normalize_text(value: str, *, remove_markup: bool = False) -> str:
    value = html.unescape(value)
    if remove_markup:
        value = re.sub(r"<[^>]*>", " ", value)
    return " ".join(value.split()).casefold()
