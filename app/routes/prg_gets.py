from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Watch
from app.routes.watches import manage_context, subscriber_from_manage_token
from app.services.tokens import (
    VerificationTokenError,
    VerificationTokenExpired,
    read_manage_prg_token,
    read_resend_verification_prg_token,
    read_watch_status_token,
)


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def prg_status_code(value: object) -> int:
    """Keep signed PRG status codes in the normal HTTP range."""
    if isinstance(value, int) and 100 <= value <= 599:
        return value

    return status.HTTP_200_OK


@router.get("/watches/status", response_class=HTMLResponse)
async def show_watch_status(
    request: Request,
    token: str,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Render the watch-created page after POST /watches redirects here."""
    try:
        data = read_watch_status_token(token)
    except VerificationTokenError:
        return templates.TemplateResponse(
            request=request,
            name="whoops.html",
            context={"message": "That watch status link is invalid or expired."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    watch = db.get(Watch, data["watch_id"])

    if watch is None:
        return templates.TemplateResponse(
            request=request,
            name="whoops.html",
            context={
                "message": "That watch could not be found.",
            },
            status_code=status.HTTP_404_NOT_FOUND,
        )

    return templates.TemplateResponse(
        request=request,
        name="watch_created.html",
        context={
            "success": True,
            "message": data.get("message"),
            "resend_message": data.get("resend_message"),
            "watch": watch,
            "email": watch.subscriber.email,
            "subject": watch.subject,
            "catalog_number": watch.catalog_number,
        },
        status_code=prg_status_code(data.get("status_code")),
    )


@router.get("/watches/resend-status", response_class=HTMLResponse)
async def show_resend_verification_status(
    request: Request,
    token: str,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Render the resend result after POST resend-verification redirects here."""
    try:
        data = read_resend_verification_prg_token(token)
    except VerificationTokenError:
        return templates.TemplateResponse(
            request=request,
            name="whoops.html",
            context={"message": "That verification status link is invalid."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    watch = db.get(Watch, data["watch_id"])

    if watch is None:
        return templates.TemplateResponse(
            request=request,
            name="whoops.html",
            context={"message": "That pending watch could not be found."},
            status_code=status.HTTP_404_NOT_FOUND,
        )

    return templates.TemplateResponse(
        request=request,
        name="watch_created.html",
        context={
            "success": True,
            "message": data.get("message"),
            "resend_message": data.get("resend_message"),
            "watch": watch,
            "email": watch.subscriber.email,
            "subject": watch.subject,
            "catalog_number": watch.catalog_number,
        },
        status_code=prg_status_code(data.get("status_code")),
    )


@router.get("/manage/requested", response_class=HTMLResponse)
def show_manage_requested(
    request: Request,
    token: str,
) -> HTMLResponse:
    """Render the manage-link request result after POST /manage redirects here."""
    try:
        data = read_manage_prg_token(token)
    except VerificationTokenError:
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
            mode="requested",
            resend_token=str(data["resend_token"]),
            resend_message=data.get("resend_message"),
        ),
        status_code=prg_status_code(data.get("status_code")),
    )


@router.get("/manage/{token}", response_class=HTMLResponse)
def show_managed_watches(
    request: Request,
    token: str,
    message: str | None = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Render the manage page after watch removal POSTs redirect here."""
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
            message=message,
        ),
    )
