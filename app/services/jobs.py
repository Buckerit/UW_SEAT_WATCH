from __future__ import annotations

from app.services.outbox import send_pending_notifications
from app.services.polling import PollSummary, poll_all_watches


async def run_polling_job() -> PollSummary:
    summary = await poll_all_watches()

    send_pending_notifications()

    return summary
