from __future__ import annotations

import json
import logging

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Notification, Watch, utc_now
from app.services.email import EmailDeliveryError
from app.services.notifications import send_opening_alert


logger = logging.getLogger(__name__)


def send_pending_notifications() -> int:
    sent_count = 0

    with SessionLocal() as db:
        notifications = db.scalars(
            select(Notification)
            .where(Notification.sent_at.is_(None))
            .order_by(Notification.created_at)
        ).all()

        for notification in notifications:
            watch = db.get(
                Watch,
                notification.watch_id,
            )

            if watch is None or not watch.active:
                notification.sent_at = utc_now()
                notification.last_error = (
                    "Notification cancelled because the watch "
                    "no longer exists or is inactive."
                )

                logger.info(
                    (
                        "Cancelled notification %s because watch %s "
                        "is inactive or missing."
                    ),
                    notification.id,
                    notification.watch_id,
                )

                continue

            try:
                payload = json.loads(
                    notification.payload
                )

                if notification.kind == "section_open":
                    send_opening_alert(
                        watch_id=notification.watch_id,
                        email=payload["email"],
                        subject=payload["subject"],
                        catalog_number=payload[
                            "catalog_number"
                        ],
                        section_name=payload[
                            "section_name"
                        ],
                        current_enrollment=payload[
                            "current_enrollment"
                        ],
                        current_capacity=payload[
                            "current_capacity"
                        ],
                    )

                else:
                    notification.last_error = (
                        f"Unknown notification kind: "
                        f"{notification.kind}"
                    )
                    continue

            except (
                EmailDeliveryError,
                KeyError,
                ValueError,
                TypeError,
            ) as exc:
                notification.attempt_count += 1
                notification.last_error = str(exc)

                logger.exception(
                    "Failed notification %s",
                    notification.id,
                )

                continue

            notification.sent_at = utc_now()
            watch.last_notified_at = notification.sent_at
            notification.attempt_count += 1
            notification.last_error = None

            sent_count += 1

        db.commit()

    return sent_count
