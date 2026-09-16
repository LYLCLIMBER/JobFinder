from collections.abc import Mapping

from job_page_finder.runtime import build_application
from job_page_finder.settings import RuntimeSettings
from job_page_finder.task_protocol import TaskRequest, TaskResult


async def run_task(
    request: TaskRequest | Mapping[str, object],
    *,
    settings: RuntimeSettings | None = None,
) -> TaskResult:
    application = build_application(settings)
    return await application.run(request)
