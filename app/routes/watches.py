from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import Notification, SectionState, Subscriber, Watch, utc_now
from app.waterloo.client import WaterlooClientError, fetch_course_html
from app.waterloo.parser import parse_course_sections

from fastapi.templating import Jinja2Templates

from app.services.notifications import send_watch_verification_email

from app.services.tokens import (
    VerificationTokenError,
    VerificationTokenExpired,
    read_watch_unsubscribe_token,
    read_watch_verification_token,
)


router = APIRouter()

templates = Jinja2Templates(directory="app/templates")

EMAIL_PATTERN = re.compile(
    r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
)

MAX_VERIFICATION_RESENDS = 3
VERIFICATION_RESEND_COOLDOWN = timedelta(minutes=1)


def development_verification_url(verification_url: str) -> str | None:
    if get_settings().app_env.lower() in {"local", "development"}:
        return verification_url

    return None


def aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc)


def send_and_mark_verification_email(
    db: Session,
    watch: Watch,
) -> str:
    verification_url = send_watch_verification_email(
        watch_id=watch.id,
        email=watch.subscriber.email,
        subject=watch.subject,
        catalog_number=watch.catalog_number,
        section_name=watch.section_name,
    )
    watch.verification_email_sent_at = utc_now()
    db.commit()
    db.refresh(watch)
    return verification_url


def save_section_baseline(
    db: Session,
    *,
    level: str,
    term: str,
    subject: str,
    catalog_number: str,
    section,
) -> None:
    """Store the current section state so future polling sees changes."""
    checked_at = utc_now()

    state = db.scalar(
        select(SectionState).where(
            SectionState.term == term,
            SectionState.class_number == section.class_number,
        )
    )

    if state is None:
        state = SectionState(
            level=level,
            term=term,
            subject=subject,
            catalog_number=catalog_number,
            class_number=section.class_number,
            section_name=section.section_number,
            enrollment_capacity=section.enrollment_capacity,
            enrollment_total=section.enrollment_total,
            waitlist_capacity=section.waitlist_capacity,
            waitlist_total=section.waitlist_total,
            last_checked_at=checked_at,
        )
        db.add(state)
        return

    state.level = level
    state.subject = subject
    state.catalog_number = catalog_number
    state.section_name = section.section_number
    state.enrollment_capacity = section.enrollment_capacity
    state.enrollment_total = section.enrollment_total
    state.waitlist_capacity = section.waitlist_capacity
    state.waitlist_total = section.waitlist_total
    state.last_checked_at = checked_at


def queue_opening_notification(db: Session, watch: Watch) -> None:
    """Queue one alert email for the outbox worker to send."""
    state = db.scalar(
        select(SectionState).where(
            SectionState.term == watch.term,
            SectionState.class_number == watch.class_number,
        )
    )

    current_capacity = (
        state.enrollment_capacity
        if state is not None
        else 0
    )
    current_enrollment = (
        state.enrollment_total
        if state is not None
        else 0
    )

    payload = {
        "email": watch.subscriber.email,
        "term": watch.term,
        "subject": watch.subject,
        "catalog_number": watch.catalog_number,
        "class_number": watch.class_number,
        "section_name": watch.section_name,
        "current_capacity": current_capacity,
        "current_enrollment": current_enrollment,
    }

    db.add(
        Notification(
            watch_id=watch.id,
            kind="section_open",
            payload=json.dumps(payload),
        )
    )


@router.post("/watches", response_class=HTMLResponse)
async def create_watch(
    request: Request,
    level: str = Form(...),
    term: str = Form(...),
    subject: str = Form(...),
    catalog_number: str = Form(...),
    class_number: str = Form(...),
    email: str = Form(...),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    normalized_level = level.strip().lower()
    normalized_term = term.strip()
    normalized_subject = subject.strip().upper()
    normalized_catalog_number = catalog_number.strip().upper()
    normalized_class_number = class_number.strip()
    normalized_email = email.strip().lower()

    if (
        len(normalized_email) > 320
        or EMAIL_PATTERN.fullmatch(normalized_email) is None
    ):
        return templates.TemplateResponse(
            request=request,
            name="watch_created.html",
            context={
                "success": False,
                "message": "Enter a valid email address.",
            },
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    try:
        html = await fetch_course_html(
            level=normalized_level,
            term=normalized_term,
            subject=normalized_subject,
            catalog_num=normalized_catalog_number,
        )
        
        sections = parse_course_sections(html)
        
    except WaterlooClientError:
        return templates.TemplateResponse(
            request=request,
            name="watch_created.html",
            context={
                "success": False,
                "message": (
                    "Waterloo's Schedule of Classes could not be "
                    "checked. Please try again shortly."
                ),
            },
            status_code=status.HTTP_502_BAD_GATEWAY,
        )
    
    selected_section = next(
        (
            section
            for section in sections
            if section.class_number
            == normalized_class_number
        ),
        None,
    )
    
    if selected_section is None:
        return templates.TemplateResponse(
            request=request,
            name="watch_created.html",
            context={
                "success": False,
                "message": (
                    "That section could not be found in Waterloo's "
                    "current course listing."
                ),
            },
            status_code=status.HTTP_404_NOT_FOUND,
        )
        
    subscriber = db.scalar(
        select(Subscriber).where(Subscriber.email == normalized_email)
    )
    
    if subscriber is None:
        subscriber = Subscriber(
            email=normalized_email,
        )

        db.add(subscriber)
        db.flush()
        
    existing_watch = db.scalar(
        select(Watch).where(
            Watch.subscriber_id == subscriber.id,
            Watch.term == normalized_term,
            Watch.class_number
            == selected_section.class_number,
        )
    )
    
    if existing_watch is not None:
        if not existing_watch.active:
            save_section_baseline(
                db,
                level=normalized_level,
                term=normalized_term,
                subject=normalized_subject,
                catalog_number=normalized_catalog_number,
                section=selected_section,
            )
            existing_watch.last_seen_open = selected_section.appears_open
            db.commit()
            db.refresh(existing_watch)

            verification_url = send_and_mark_verification_email(
                db,
                existing_watch,
            )

            return templates.TemplateResponse(
                request=request,
                name="watch_created.html",
                context={
                    "success": True,
                    "message": (
                        "This watch was already pending. We sent "
                        "you a fresh verification email."
                    ),
                    "watch": existing_watch,
                    "email": normalized_email,
                    "subject": normalized_subject,
                    "catalog_number": normalized_catalog_number,
                    "verification_url": development_verification_url(
                        verification_url
                    ),
                },
            )

        return templates.TemplateResponse(
            request=request,
            name="watch_created.html",
            context={
                "success": True,
                "message": (
                    f"{normalized_email} is already watching "
                    f"{normalized_subject} "
                    f"{normalized_catalog_number} "
                    f"{selected_section.section_number}."
                ),
                "watch": existing_watch,
                "email": normalized_email,
                "subject": normalized_subject,
                "catalog_number": normalized_catalog_number,
            },
        )
        
    watch = Watch(
        level=normalized_level,
        term=normalized_term,
        subject=normalized_subject,
        catalog_number=normalized_catalog_number,
        class_number=selected_section.class_number,
        section_name=selected_section.section_number,
        active=False,
        last_seen_open=selected_section.appears_open,
    )
    
    subscriber.watches.append(watch)
    save_section_baseline(
        db,
        level=normalized_level,
        term=normalized_term,
        subject=normalized_subject,
        catalog_number=normalized_catalog_number,
        section=selected_section,
    )

    try:
        db.commit()
        db.refresh(watch)
        verification_url = send_and_mark_verification_email(
            db,
            watch,
        )

    except IntegrityError:
        db.rollback()

        return templates.TemplateResponse(
            request=request,
            name="watch_created.html",
            context={
                "success": False,
                "message": (
                    "That watch already exists or could not be saved."
                ),
            },
            status_code=status.HTTP_409_CONFLICT,
        )

    return templates.TemplateResponse(
        request=request,
        name="watch_created.html",
        context={
            "success": True,
            "message": (
                "The watch was saved. It is currently pending "
                "email verification."
            ),
            "watch": watch,
            "email": normalized_email,
            "subject": normalized_subject,
            "catalog_number": normalized_catalog_number,
            "verification_url": development_verification_url(
                verification_url
            ),
        },
        status_code=status.HTTP_201_CREATED,
    )


@router.post(
    "/watches/{watch_id}/resend-verification",
    response_class=HTMLResponse,
)
def resend_watch_verification(
    request: Request,
    watch_id: int,
    email: str = Form(...),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    normalized_email = email.strip().lower()
    watch = db.get(Watch, watch_id)

    if watch is None or watch.subscriber.email != normalized_email:
        return templates.TemplateResponse(
            request=request,
            name="watch_created.html",
            context={
                "success": False,
                "message": "That pending watch could not be found.",
            },
            status_code=status.HTTP_404_NOT_FOUND,
        )

    if watch.active:
        return templates.TemplateResponse(
            request=request,
            name="watch_created.html",
            context={
                "success": True,
                "message": "This watch is already active.",
                "watch": watch,
                "email": watch.subscriber.email,
                "subject": watch.subject,
                "catalog_number": watch.catalog_number,
            },
        )

    if watch.verification_resend_count >= MAX_VERIFICATION_RESENDS:
        return templates.TemplateResponse(
            request=request,
            name="watch_created.html",
            context={
                "success": True,
                "message": (
                    "This watch is still pending email verification."
                ),
                "resend_message": (
                    "You've used all 3 verification resends for this watch."
                ),
                "watch": watch,
                "email": watch.subscriber.email,
                "subject": watch.subject,
                "catalog_number": watch.catalog_number,
            },
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    last_sent_at = aware_utc(watch.verification_email_sent_at)
    now = utc_now()

    if (
        last_sent_at is not None
        and now - last_sent_at < VERIFICATION_RESEND_COOLDOWN
    ):
        seconds_left = int(
            (
                VERIFICATION_RESEND_COOLDOWN
                - (now - last_sent_at)
            ).total_seconds()
        )

        return templates.TemplateResponse(
            request=request,
            name="watch_created.html",
            context={
                "success": True,
                "message": (
                    "This watch is still pending email verification."
                ),
                "resend_message": (
                    f"Wait about {max(seconds_left, 1)} seconds before "
                    "resending."
                ),
                "watch": watch,
                "email": watch.subscriber.email,
                "subject": watch.subject,
                "catalog_number": watch.catalog_number,
            },
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    watch.verification_resend_count += 1
    verification_url = send_and_mark_verification_email(db, watch)

    return templates.TemplateResponse(
        request=request,
        name="watch_created.html",
        context={
            "success": True,
            "message": "We sent another verification email.",
            "resend_message": (
                f"Resend {watch.verification_resend_count} of "
                f"{MAX_VERIFICATION_RESENDS} used."
            ),
            "watch": watch,
            "email": watch.subscriber.email,
            "subject": watch.subject,
            "catalog_number": watch.catalog_number,
            "verification_url": development_verification_url(
                verification_url
            ),
        },
    )

@router.get("/verify", response_class=HTMLResponse)
def verify_watch(request: Request, token: str, db: Session = Depends(get_db)) -> HTMLResponse:
    try:
        watch_id = read_watch_verification_token(token)

    except VerificationTokenExpired:
        return templates.TemplateResponse(
            request=request,
            name="verified.html",
            context={
                "success": False,
                "message": "This verification link has expired.",
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    except VerificationTokenError:
        return templates.TemplateResponse(
            request=request,
            name="verified.html",
            context={
                "success": False,
                "message": "This verification link is invalid.",
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    watch = db.get(Watch, watch_id)

    if watch is None:
        return templates.TemplateResponse(
            request=request,
            name="verified.html",
            context={
                "success": False,
                "message": "This watch no longer exists.",
            },
            status_code=status.HTTP_404_NOT_FOUND,
        )

    if watch.active and watch.confirmed_at is not None:
        return templates.TemplateResponse(
            request=request,
            name="verified.html",
            context={
                "success": True,
                "already_verified": True,
                "message": "This watch is already active.",
                "watch": watch,
            },
        )

    verified_at = datetime.now(timezone.utc)

    watch.active = True
    watch.confirmed_at = verified_at

    if watch.subscriber.verified_at is None:
        watch.subscriber.verified_at = verified_at

    if watch.last_seen_open and watch.last_notified_at is None:
        queue_opening_notification(db, watch)

    db.commit()
    db.refresh(watch)

    return templates.TemplateResponse(
        request=request,
        name="verified.html",
        context={
            "success": True,
            "already_verified": False,
            "message": "Your watch is now active.",
            "watch": watch,
        },
    )


@router.get(
    "/unsubscribe",
    response_class=HTMLResponse,
)
def unsubscribe_watch(
    request: Request,
    token: str,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    try:
        watch_id = read_watch_unsubscribe_token(token)

    except VerificationTokenError:
        return templates.TemplateResponse(
            request=request,
            name="unsubscribed.html",
            context={
                "success": False,
                "message": "This unsubscribe link is invalid.",
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    watch = db.get(Watch, watch_id)

    if watch is None:
        return templates.TemplateResponse(
            request=request,
            name="unsubscribed.html",
            context={
                "success": False,
                "message": "This watch no longer exists.",
            },
            status_code=status.HTTP_404_NOT_FOUND,
        )

    if not watch.active:
        return templates.TemplateResponse(
            request=request,
            name="unsubscribed.html",
            context={
                "success": True,
                "message": "This watch is already inactive.",
                "watch": watch,
            },
        )

    watch.active = False

    db.commit()

    return templates.TemplateResponse(
        request=request,
        name="unsubscribed.html",
        context={
            "success": True,
            "message": "You will no longer receive alerts for this section.",
            "watch": watch,
        },
    )
