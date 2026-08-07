from __future__ import annotations

from app.config import get_settings
from app.services.email import send_email
from app.services.tokens import (
    create_watch_unsubscribe_token,
    create_watch_verification_token,
)


def send_watch_verification_email(
    *,
    watch_id: int,
    email: str,
    subject: str,
    catalog_number: str,
    section_name: str,
) -> str:
    settings = get_settings()

    token = create_watch_verification_token(watch_id)

    verification_url = (
        f"{settings.base_url.rstrip('/')}"
        f"/verify?token={token}"
    )

    email_subject = (
        f"Verify your watch for "
        f"{subject} {catalog_number} {section_name}"
    )

    text = f"""
Verify your UW Seat Watch

Course: {subject} {catalog_number}
Section: {section_name}

Activate your watch:
{verification_url}

If you did not request this watch, you can ignore this email.
""".strip()

    html = f"""
    <h2>Verify your UW Seat Watch</h2>

    <p>
        <strong>{subject} {catalog_number}</strong>
        &mdash; {section_name}
    </p>

    <p>
        <a href="{verification_url}">
            Activate this watch
        </a>
    </p>

    <p>
        If you did not request this watch,
        you can ignore this email.
    </p>
    """

    send_email(
        to=email,
        subject=email_subject,
        html=html,
        text=text,
    )

    return verification_url


def send_opening_alert(
    *,
    watch_id: int,
    email: str,
    subject: str,
    catalog_number: str,
    section_name: str,
    current_enrollment: int,
    current_capacity: int,
) -> None:
    settings = get_settings()

    token = create_watch_unsubscribe_token(watch_id)

    unsubscribe_url = (
        f"{settings.base_url.rstrip('/')}"
        f"/unsubscribe?token={token}"
    )

    available_seats = max(
        current_capacity - current_enrollment,
        0,
    )

    email_subject = (
        f"Potential seat available - "
        f"{subject} {catalog_number} {section_name}"
    )

    text = f"""
Potential seat availability detected.

Course: {subject} {catalog_number}
Section: {section_name}

Enrollment:
{current_enrollment}/{current_capacity}

Potential available seats:
{available_seats}

Waterloo's public Schedule of Classes currently appears to show
availability.

This does NOT guarantee enrollment. Reserved seats, prerequisites,
related components, restrictions, and Quest eligibility may still
prevent registration.

Stop watching:
{unsubscribe_url}
""".strip()

    html = f"""
    <h2>Potential seat available</h2>

    <p>
        <strong>{subject} {catalog_number}</strong>
        &mdash; {section_name}
    </p>

    <p>
        Enrollment:
        <strong>
            {current_enrollment}/{current_capacity}
        </strong>
    </p>

    <p>
        Potential available seats:
        <strong>{available_seats}</strong>
    </p>

    <p>
        Waterloo's public Schedule of Classes currently appears
        to show availability.
    </p>

    <p>
        <strong>Availability is not guaranteed.</strong>
        Reserved seats, prerequisites, related components,
        restrictions, and Quest eligibility may still prevent
        enrollment.
    </p>

    <p>
        <a href="{unsubscribe_url}">
            Stop watching this section
        </a>
    </p>
    """

    send_email(
        to=email,
        subject=email_subject,
        html=html,
        text=text,
    )
