from __future__ import annotations

from app.config import get_settings
from app.services.audit import log_event, mask_email
from app.services.email import send_email
from app.services.tokens import (
    create_manage_watches_token,
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

    unsubscribe_token = create_watch_unsubscribe_token(watch_id)

    unsubscribe_url = (
        f"{settings.base_url.rstrip('/')}"
        f"/unsubscribe?token={unsubscribe_token}"
    )

    course_label = f"{subject} {catalog_number} {section_name}"

    email_subject = f"Verify your UW Seat Watch alert for {course_label}"

    text = f"""
Verify this UW Seat Watch alert

Section: {course_label}

Verify and activate:
{verification_url}

To cancel this watch:
{unsubscribe_url}

If you did not request this watch, you can ignore this email.
""".strip()

    html = f"""
    <h2>Verify this UW Seat Watch alert</h2>

    <p>
        Section: <strong>{course_label}</strong>
    </p>

    <p>
        <a href="{verification_url}">
            Verify and activate
        </a>
    </p>

    <p>
        If you did not request this watch,
        you can ignore this email.
    </p>

    <p>
        <a href="{unsubscribe_url}">
            Cancel this watch
        </a>
    </p>
    """

    send_email(
        to=email,
        subject=email_subject,
        html=html,
        text=text,
    )

    log_event(
        "email",
        "verification_sent",
        email=mask_email(email),
        subject=subject,
        catalog=catalog_number,
        section=section_name,
        watch_id=watch_id,
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

    course_label = f"{subject} {catalog_number} {section_name}"

    email_subject = f"{course_label} may have a seat open"

    text = f"""
UW Seat Watch found a possible opening.

Section: {course_label}

Enrollment:
{current_enrollment}/{current_capacity}

Potential available seats:
{available_seats}

Waterloo's public Schedule of Classes currently appears to show
availability.

This does NOT guarantee enrollment. Reserved seats, prerequisites,
related components, restrictions, and Quest eligibility may still
prevent registration.

To cancel this watch:
{unsubscribe_url}
""".strip()

    html = f"""
    <h2>Possible seat opening</h2>

    <p>
        Section: <strong>{course_label}</strong>
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
            Cancel this watch
        </a>
    </p>
    """

    send_email(
        to=email,
        subject=email_subject,
        html=html,
        text=text,
    )

    log_event(
        "email",
        "opening_alert_sent",
        email=mask_email(email),
        subject=subject,
        catalog=catalog_number,
        section=section_name,
        watch_id=watch_id,
        available_seats=available_seats,
    )


def send_manage_watches_email(
    *,
    subscriber_id: int,
    email: str,
) -> str:
    settings = get_settings()

    token = create_manage_watches_token(
        subscriber_id=subscriber_id,
        email=email,
    )

    manage_url = (
        f"{settings.base_url.rstrip('/')}"
        f"/manage/{token}"
    )

    text = f"""
Someone requested a link to manage the UW Seat Watch watches associated with this email.

Manage my watches:
{manage_url}

This link expires shortly.

If you did not request this, you can ignore this email.
""".strip()

    html = f"""
    <h2>Manage your UW Seat Watch watches</h2>

    <p>
        Someone requested a link to manage the UW Seat Watch
        watches associated with this email.
    </p>

    <p>
        <a href="{manage_url}">
            Manage my watches
        </a>
    </p>

    <p>
        This link expires shortly.
    </p>

    <p>
        If you did not request this, you can ignore this email.
    </p>
    """

    send_email(
        to=email,
        subject="Manage your UW Seat Watch watches",
        html=html,
        text=text,
    )

    log_event(
        "email",
        "manage_link_sent",
        email=mask_email(email),
        subscriber_id=subscriber_id,
    )

    return manage_url
