from __future__ import annotations

import logging

import resend

from app.config import get_settings


logger = logging.getLogger(__name__)


class EmailDeliveryError(RuntimeError):
    pass


def send_email(
    *,
    to: str,
    subject: str,
    html: str,
    text: str,
) -> None:
    settings = get_settings()

    if settings.email_backend == "console":
        print()
        print("=" * 70)
        print("UW SEAT WATCH - DEVELOPMENT EMAIL")
        print(f"To: {to}")
        print(f"Subject: {subject}")
        print()
        print(text)
        print("=" * 70)
        print()

        return

    if settings.email_backend == "resend":
        if not settings.resend_api_key:
            raise EmailDeliveryError(
                "RESEND_API_KEY is missing."
            )

        resend.api_key = settings.resend_api_key

        try:
            resend.Emails.send(
                {
                    "from": settings.from_email,
                    "to": [to],
                    "subject": subject,
                    "html": html,
                    "text": text,
                }
            )

        except Exception as exc:
            logger.exception(
                "Resend failed while sending email to %s",
                to,
            )

            raise EmailDeliveryError(
                "Email could not be delivered."
            ) from exc

        return

    raise EmailDeliveryError(
        f"Unsupported EMAIL_BACKEND: "
        f"{settings.email_backend}"
    )
