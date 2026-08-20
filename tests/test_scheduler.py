from __future__ import annotations

from app.services.scheduler import create_scheduler
from app.services.jobs import run_openapi_polling_job
from app.services.outbox import send_pending_notifications


def test_scheduler_outbox_retry_does_not_use_polling_job() -> None:
    scheduler = create_scheduler()

    jobs = {
        job.id: job
        for job in scheduler.get_jobs()
    }

    assert "send_pending_notifications" in jobs
    assert jobs["send_pending_notifications"].func is send_pending_notifications
    assert str(jobs["send_pending_notifications"].trigger) == "cron[minute='*/5']"
    assert "poll_watches_openapi" in jobs
    assert jobs["poll_watches_openapi"].func is run_openapi_polling_job
    assert str(jobs["poll_watches_openapi"].trigger) == "cron[minute='*/5']"
