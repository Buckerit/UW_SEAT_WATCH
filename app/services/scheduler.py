from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import get_settings
from app.services.cleanup import cleanup_ended_term_watches
from app.services.jobs import run_openapi_polling_job, run_polling_job
from app.services.outbox import send_pending_notifications


logger = logging.getLogger(__name__)


def create_scheduler() -> AsyncIOScheduler:
    settings = get_settings()
    final_poll_minute = settings.poll_minutes.split(",")[0].strip()

    scheduler = AsyncIOScheduler(
        timezone=settings.timezone,
    )

    scheduler.add_job(
        run_polling_job,
        trigger=CronTrigger(
            minute=settings.poll_minutes,
            hour=f"{settings.poll_start_hour}-{settings.poll_end_hour - 1}",
            timezone=settings.timezone,
        ),
        id="poll_watches_daytime",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )

    scheduler.add_job(
        run_polling_job,
        trigger=CronTrigger(
            minute=final_poll_minute,
            hour=str(settings.poll_end_hour),
            timezone=settings.timezone,
        ),
        id="poll_watches_final",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )

    # Retry unsent emails every 5 minutes.
    # This job never contacts Waterloo; it only reads our notifications table.
    scheduler.add_job(
        send_pending_notifications,
        trigger=CronTrigger(
            minute="*/5",
            timezone=settings.timezone,
        ),
        id="send_pending_notifications",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )

    scheduler.add_job(
        run_openapi_polling_job,
        trigger=CronTrigger(
            minute="*/5",
            timezone=settings.timezone,
        ),
        id="poll_watches_openapi",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )

    scheduler.add_job(
        cleanup_ended_term_watches,
        trigger=CronTrigger(
            month="1,3,5,7,9,11",
            day=1,
            hour=3,
            minute=15,
            timezone=settings.timezone,
        ),
        id="cleanup_ended_term_watches",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )

    return scheduler
