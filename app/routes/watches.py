from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Subscriber, Watch
from app.waterloo.client import WaterlooClientError, fetch_course_html
from app.waterloo.parser import parse_course_sections

from fastapi.templating import Jinja2Templates

from datetime import datetime, timezone

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
        db.rollback()

        return templates.TemplateResponse(
            request=request,
            name="watch_created.html",
            context={
                "success": True,
                "message": (
                    f"{normalized_email} already has a watch for "
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
    )
    
    subscriber.watches.append(watch)

    try:
        db.commit()
        db.refresh(watch)
        verification_url = send_watch_verification_email(
            watch_id=watch.id,
            email=subscriber.email,
            subject=watch.subject,
            catalog_number=watch.catalog_number,
            section_name=watch.section_name,
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
            "verification_url": verification_url,
        },
        status_code=status.HTTP_201_CREATED,
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
