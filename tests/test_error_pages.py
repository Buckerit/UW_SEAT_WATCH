from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from app.main import (
    app,
    unexpected_exception_handler,
)


def test_missing_page_uses_whoops_page() -> None:
    client = TestClient(app)

    response = client.get("/does-not-exist")

    assert response.status_code == 404
    assert "Whoops" in response.text
    assert "That page does not exist." in response.text


def test_wrong_method_uses_whoops_page() -> None:
    client = TestClient(app)

    response = client.get("/watches")

    assert response.status_code == 405
    assert "Whoops" in response.text
    assert "cannot be opened directly" in response.text


def test_unexpected_error_uses_whoops_page() -> None:
    test_app = FastAPI()
    test_app.mount(
        "/static",
        StaticFiles(directory="app/static"),
        name="static",
    )
    test_app.add_exception_handler(
        Exception,
        unexpected_exception_handler,
    )

    @test_app.get("/test-only-error")
    async def test_only_error() -> None:
        raise RuntimeError("boom")

    client = TestClient(test_app, raise_server_exceptions=False)

    response = client.get("/test-only-error")

    assert response.status_code == 500
    assert "Whoops" in response.text
    assert "logged for the site owner" in response.text
