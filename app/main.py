from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import get_settings
from app.routes.search import router as search_router
from app.routes.watches import router as watches_router
from app.services.scheduler import create_scheduler


settings = get_settings()
templates = Jinja2Templates(directory="app/templates")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    del app

    scheduler = None

    if settings.scheduler_enabled:
        scheduler = create_scheduler()
        scheduler.start()

        print("UW Seat Watch scheduler started.")

    try:
        yield

    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)
            print("UW Seat Watch scheduler stopped.")


app = FastAPI(
    title=settings.app_name,
    debug=settings.debug,
    lifespan=lifespan,
)


app.mount(
    "/static",
    StaticFiles(directory="app/static"),
    name="static",
)


app.include_router(search_router)
app.include_router(watches_router)


def whoops_response(
    request: Request,
    *,
    status_code: int,
    message: str,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="whoops.html",
        context={
            "status_code": status_code,
            "message": message,
        },
        status_code=status_code,
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(
    request: Request,
    exc: StarletteHTTPException,
) -> HTMLResponse:
    if exc.status_code == 404:
        message = "That page does not exist."
    elif exc.status_code == 405:
        message = "That page cannot be opened directly."
    else:
        message = "That request cannot be completed right now."

    return whoops_response(
        request,
        status_code=exc.status_code,
        message=message,
    )


@app.exception_handler(Exception)
async def unexpected_exception_handler(
    request: Request,
    exc: Exception,
) -> HTMLResponse:
    del exc

    return whoops_response(
        request,
        status_code=500,
        message=(
            "Something went wrong on our end. The issue has been "
            "logged for the site owner."
        ),
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
    }
