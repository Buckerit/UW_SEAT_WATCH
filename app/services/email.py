from __future__ import annotations

from email.utils import parseaddr
import logging

import httpx

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

    if settings.email_backend == "brevo":
        if not settings.brevo_api_key:
            raise EmailDeliveryError(
                "BREVO_API_KEY is missing."
            )

        sender_name, sender_email = parseaddr(settings.from_email)

        if not sender_email:
            raise EmailDeliveryError(
                "FROM_EMAIL must contain a valid sender email."
            )

        try:
            response = httpx.post(
                "https://api.brevo.com/v3/smtp/email",
                headers={
                    "accept": "application/json",
                    "api-key": settings.brevo_api_key,
                    "content-type": "application/json",
                },
                json={
                    "sender": {
                        "name": sender_name or sender_email,
                        "email": sender_email,
                    },
                    "to": [
                        {
                            "email": to,
                        }
                    ],
                    "subject": subject,
                    "htmlContent": html,
                    "textContent": text,
                },
                timeout=15.0,
            )
            response.raise_for_status()

        except httpx.HTTPError as exc:
            logger.exception(
                "Brevo failed while sending email to %s",
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
