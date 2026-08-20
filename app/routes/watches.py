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
from app.services.audit import log_event, mask_email
from app.waterloo.client import WaterlooClientError, fetch_course_html
from app.waterloo.parser import parse_course_sections

from fastapi.templating import Jinja2Templates

from app.services.notifications import (
    send_manage_watches_email,
    send_watch_verification_email,
)
from app.services.outbox import send_pending_notifications

from app.services.tokens import (
    VerificationTokenError,
    VerificationTokenExpired,
    create_manage_request_token,
    read_manage_request_token,
    read_manage_watches_token,
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
MANAGE_LINK_COOLDOWN = timedelta(minutes=1)
MAX_MANAGE_LINK_RESENDS = 2
manage_link_requests: dict[str, dict[str, object]] = {}

TERM_SEASONS = {
    "1": "Winter",
    "5": "Spring",
    "9": "Fall",
}


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


def term_label(term: str) -> str:
    if len(term) == 4 and term.startswith("1"):
        season = TERM_SEASONS.get(term[-1])

        if season is not None:
            return f"{season} 20{term[1:3]}"

    return term


def manage_link_seconds_left(email: str) -> int:
    now = utc_now()
    request_state = manage_link_requests.get(email)

    if request_state is None:
        return 0

    last_requested_at = aware_utc(
        request_state.get("last_sent_at")
    )

    if (
        last_requested_at is not None
        and now - last_requested_at < MANAGE_LINK_COOLDOWN
    ):
        return int(
            (
                MANAGE_LINK_COOLDOWN
                - (now - last_requested_at)
            ).total_seconds()
        )

    return 0


def manage_resend_count(email: str) -> int:
    request_state = manage_link_requests.get(email)

    if request_state is None:
        return 0

    return int(request_state.get("resend_count", 0))


def mark_manage_link_sent(email: str, *, is_resend: bool) -> None:
    request_state = manage_link_requests.setdefault(
        email,
        {
            "last_sent_at": None,
            "resend_count": 0,
        },
    )

    request_state["last_sent_at"] = utc_now()

    if is_resend:
        request_state["resend_count"] = (
            int(request_state.get("resend_count", 0)) + 1
        )


def manage_context(
    *,
    mode: str,
    subscriber: Subscriber | None = None,
    token: str | None = None,
    email: str = "",
    resend_token: str | None = None,
    message: str | None = None,
    resend_message: str | None = None,
) -> dict[str, object]:
    watches = subscriber.watches if subscriber is not None else []

    return {
        "mode": mode,
        "subscriber": subscriber,
        "masked_email": (
            mask_email(subscriber.email)
            if subscriber is not None
            else ""
        ),
        "watches": watches,
        "token": token,
        "email": email,
        "resend_token": resend_token,
        "message": message,
        "resend_message": resend_message,
        "term_label": term_label,
    }


def subscriber_from_manage_token(
    db: Session,
    token: str,
) -> Subscriber:
    subscriber_id, email = read_manage_watches_token(token)
    subscriber = db.scalar(
        select(Subscriber).where(
            Subscriber.id == subscriber_id,
            Subscriber.email == email,
        )
    )

    if subscriber is None:
        raise VerificationTokenError(
            "This management link is invalid."
        )

    return subscriber


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


@router.get("/manage", response_class=HTMLResponse)
def show_manage_request(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="manage.html",
        context=manage_context(mode="request"),
    )


@router.post("/manage", response_class=HTMLResponse)
def request_manage_link(
    request: Request,
    email: str = Form(...),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    normalized_email = email.strip().lower()

    if (
        len(normalized_email) > 320
        or EMAIL_PATTERN.fullmatch(normalized_email) is None
    ):
        log_event(
            "watch",
            "rejected_email",
            email=mask_email(normalized_email),
            subject=normalized_subject,
            catalog=normalized_catalog_number,
            term=normalized_term,
        )

        return templates.TemplateResponse(
            request=request,
            name="manage.html",
            context={
                **manage_context(mode="request"),
                "email": normalized_email,
                "error": "Enter a valid email address.",
            },
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    seconds_left = manage_link_seconds_left(normalized_email)
    subscriber = db.scalar(
        select(Subscriber).where(Subscriber.email == normalized_email)
    )

    log_event(
        "manage",
        "link_requested",
        email=mask_email(normalized_email),
        email_found=subscriber is not None,
        sent=subscriber is not None and seconds_left <= 0,
    )

    if seconds_left <= 0:
        mark_manage_link_sent(normalized_email, is_resend=False)

    if subscriber is not None and seconds_left <= 0:
        send_manage_watches_email(
            subscriber_id=subscriber.id,
            email=subscriber.email,
        )

    return templates.TemplateResponse(
        request=request,
        name="manage.html",
        context=manage_context(
            mode="requested",
            resend_token=create_manage_request_token(normalized_email),
        ),
    )


@router.post("/manage/resend", response_class=HTMLResponse)
def resend_manage_link(
    request: Request,
    resend_token: str = Form(...),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    try:
        normalized_email = read_manage_request_token(resend_token)

    except (VerificationTokenError, VerificationTokenExpired):
        return templates.TemplateResponse(
            request=request,
            name="manage.html",
            context=manage_context(mode="expired"),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if (
        len(normalized_email) > 320
        or EMAIL_PATTERN.fullmatch(normalized_email) is None
    ):
        return templates.TemplateResponse(
            request=request,
            name="manage.html",
            context={
                **manage_context(mode="request"),
                "email": normalized_email,
                "error": "Enter a valid email address.",
            },
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    resend_message = "If that email has watches, another link was sent."
    status_code = status.HTTP_200_OK

    if manage_resend_count(normalized_email) >= MAX_MANAGE_LINK_RESENDS:
        resend_message = "You've used both secure-link resends."
        status_code = status.HTTP_429_TOO_MANY_REQUESTS
    else:
        seconds_left = manage_link_seconds_left(normalized_email)

        if seconds_left > 0:
            resend_message = (
                f"Wait about {max(seconds_left, 1)} seconds before "
                "resending."
            )
            status_code = status.HTTP_429_TOO_MANY_REQUESTS
        else:
            mark_manage_link_sent(normalized_email, is_resend=True)
            subscriber = db.scalar(
                select(Subscriber).where(
                    Subscriber.email == normalized_email
                )
            )

            if subscriber is not None:
                send_manage_watches_email(
                    subscriber_id=subscriber.id,
                    email=subscriber.email,
                )

            log_event(
                "manage",
                "link_resent",
                email=mask_email(normalized_email),
                email_found=subscriber is not None,
                sent=subscriber is not None,
            )

    return templates.TemplateResponse(
        request=request,
        name="manage.html",
        context=manage_context(
            mode="requested",
            resend_token=resend_token,
            resend_message=resend_message,
        ),
        status_code=status_code,
    )


@router.get("/manage/{token}", response_class=HTMLResponse)
def show_managed_watches(
    request: Request,
    token: str,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    try:
        subscriber = subscriber_from_manage_token(db, token)

    except (VerificationTokenError, VerificationTokenExpired):
        return templates.TemplateResponse(
            request=request,
            name="manage.html",
            context=manage_context(mode="expired"),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    return templates.TemplateResponse(
        request=request,
        name="manage.html",
        context=manage_context(
            mode="list",
            subscriber=subscriber,
            token=token,
        ),
    )


@router.post(
    "/manage/{token}/watches/{watch_id}/remove",
    response_class=HTMLResponse,
)
def remove_managed_watch(
    request: Request,
    token: str,
    watch_id: int,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    try:
        subscriber = subscriber_from_manage_token(db, token)

    except (VerificationTokenError, VerificationTokenExpired):
        return templates.TemplateResponse(
            request=request,
            name="manage.html",
            context=manage_context(mode="expired"),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    watch = db.scalar(
        select(Watch).where(
            Watch.id == watch_id,
            Watch.subscriber_id == subscriber.id,
        )
    )

    if watch is not None:
        db.delete(watch)
        db.commit()
        db.refresh(subscriber)

        log_event(
            "manage",
            "watch_removed",
            email=mask_email(subscriber.email),
            subject=watch.subject,
            catalog=watch.catalog_number,
            section=watch.section_name,
            class_number=watch.class_number,
            term=watch.term,
        )

    return templates.TemplateResponse(
        request=request,
        name="manage.html",
        context=manage_context(
            mode="list",
            subscriber=subscriber,
            token=token,
            message="Watch removed.",
        ),
    )


@router.post(
    "/manage/{token}/watches/stop-all",
    response_class=HTMLResponse,
)
def stop_all_managed_watches(
    request: Request,
    token: str,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    try:
        subscriber = subscriber_from_manage_token(db, token)

    except (VerificationTokenError, VerificationTokenExpired):
        return templates.TemplateResponse(
            request=request,
            name="manage.html",
            context=manage_context(mode="expired"),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    for watch in list(subscriber.watches):
        db.delete(watch)

    db.commit()
    db.refresh(subscriber)

    log_event(
        "manage",
        "all_watches_removed",
        email=mask_email(subscriber.email),
    )

    return templates.TemplateResponse(
        request=request,
        name="manage.html",
        context=manage_context(
            mode="list",
            subscriber=subscriber,
            token=token,
            message="All watches stopped.",
        ),
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
        log_event(
            "watch",
            "section_not_found",
            email=mask_email(normalized_email),
            subject=normalized_subject,
            catalog=normalized_catalog_number,
            class_number=normalized_class_number,
            term=normalized_term,
        )

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

            log_event(
                "watch",
                "verification_resent",
                email=mask_email(normalized_email),
                subject=existing_watch.subject,
                catalog=existing_watch.catalog_number,
                section=existing_watch.section_name,
                class_number=existing_watch.class_number,
                term=existing_watch.term,
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

        log_event(
            "watch",
            "already_exists",
            email=mask_email(normalized_email),
            subject=existing_watch.subject,
            catalog=existing_watch.catalog_number,
            section=existing_watch.section_name,
            class_number=existing_watch.class_number,
            term=existing_watch.term,
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

        log_event(
            "watch",
            "created",
            email=mask_email(normalized_email),
            subject=watch.subject,
            catalog=watch.catalog_number,
            section=watch.section_name,
            class_number=watch.class_number,
            term=watch.term,
            currently_open=watch.last_seen_open,
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
        log_event(
            "verify",
            "already_active",
            email=mask_email(watch.subscriber.email),
            subject=watch.subject,
            catalog=watch.catalog_number,
            section=watch.section_name,
            class_number=watch.class_number,
            term=watch.term,
        )

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

    should_send_opening_alert = (
        watch.last_seen_open
        and watch.last_notified_at is None
    )

    if should_send_opening_alert:
        queue_opening_notification(db, watch)

    db.commit()
    db.refresh(watch)

    if should_send_opening_alert:
        send_pending_notifications(watch_id=watch.id)

    log_event(
        "verify",
        "activated",
        email=mask_email(watch.subscriber.email),
        subject=watch.subject,
        catalog=watch.catalog_number,
        section=watch.section_name,
        class_number=watch.class_number,
        term=watch.term,
    )

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
        log_event(
            "unsubscribe",
            "already_inactive",
            email=mask_email(watch.subscriber.email),
            subject=watch.subject,
            catalog=watch.catalog_number,
            section=watch.section_name,
            class_number=watch.class_number,
            term=watch.term,
        )

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

    log_event(
        "unsubscribe",
        "deactivated",
        email=mask_email(watch.subscriber.email),
        subject=watch.subject,
        catalog=watch.catalog_number,
        section=watch.section_name,
        class_number=watch.class_number,
        term=watch.term,
    )

    return templates.TemplateResponse(
        request=request,
        name="unsubscribed.html",
        context={
            "success": True,
            "message": "You will no longer receive alerts for this section.",
            "watch": watch,
        },
    )
