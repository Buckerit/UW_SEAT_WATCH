from __future__ import annotations

from app.services.outbox import send_pending_notifications
from app.services.polling import (
    PollSummary,
    poll_all_watches,
    poll_all_watches_openapi,
)


async def run_polling_job() -> PollSummary:
    summary = await poll_all_watches()

    send_pending_notifications()

    return summary


async def run_openapi_polling_job() -> PollSummary:
    summary = await poll_all_watches_openapi()

    send_pending_notifications()

    return summary
