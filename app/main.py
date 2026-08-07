from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.routes.search import router as search_router
from app.routes.watches import router as watches_router
from app.services.scheduler import create_scheduler


settings = get_settings()


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


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
    }
