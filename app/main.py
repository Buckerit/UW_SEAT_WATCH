from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.routes.search import router as search_router


settings = get_settings()


app = FastAPI(
    title=settings.app_name,
    debug=settings.debug,
)


app.mount(
    "/static",
    StaticFiles(directory="app/static"),
    name="static",
)


app.include_router(search_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
    }