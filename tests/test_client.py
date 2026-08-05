import asyncio

import httpx
import pytest

from app.config import get_settings
from app.waterloo import client as waterloo_client


class FakeAsyncClient:
    last_instance: "FakeAsyncClient | None" = None

    def __init__(self, *, timeout: httpx.Timeout, follow_redirects: bool) -> None:
        self.timeout = timeout
        self.follow_redirects = follow_redirects
        self.post_url: str | None = None
        self.post_data: dict[str, str] | None = None
        self.post_headers: dict[str, str] | None = None
        FakeAsyncClient.last_instance = self

    async def __aenter__(self) -> "FakeAsyncClient":
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    async def post(
        self,
        url: str,
        *,
        data: dict[str, str],
        headers: dict[str, str],
    ) -> httpx.Response:
        self.post_url = url
        self.post_data = data
        self.post_headers = headers
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            request=request,
            text="<html><h1>Schedule of Classes</h1></html>",
        )


class TimeoutAsyncClient:
    def __init__(self, *, timeout: httpx.Timeout, follow_redirects: bool) -> None:
        self.timeout = timeout
        self.follow_redirects = follow_redirects

    async def __aenter__(self) -> "TimeoutAsyncClient":
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    async def post(
        self,
        url: str,
        *,
        data: dict[str, str],
        headers: dict[str, str],
    ) -> httpx.Response:
        raise httpx.TimeoutException("request timed out")


class ErrorStatusAsyncClient:
    def __init__(self, *, timeout: httpx.Timeout, follow_redirects: bool) -> None:
        self.timeout = timeout
        self.follow_redirects = follow_redirects

    async def __aenter__(self) -> "ErrorStatusAsyncClient":
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    async def post(
        self,
        url: str,
        *,
        data: dict[str, str],
        headers: dict[str, str],
    ) -> httpx.Response:
        request = httpx.Request("POST", url)
        return httpx.Response(500, request=request, text="server error")


class UnexpectedHtmlAsyncClient:
    def __init__(self, *, timeout: httpx.Timeout, follow_redirects: bool) -> None:
        self.timeout = timeout
        self.follow_redirects = follow_redirects

    async def __aenter__(self) -> "UnexpectedHtmlAsyncClient":
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    async def post(
        self,
        url: str,
        *,
        data: dict[str, str],
        headers: dict[str, str],
    ) -> httpx.Response:
        request = httpx.Request("POST", url)
        return httpx.Response(200, request=request, text="<html>not a schedule page</html>")


def test_fetch_course_html_posts_expected_form_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(waterloo_client.httpx, "AsyncClient", FakeAsyncClient)

    html = asyncio.run(
        waterloo_client.fetch_course_html(
            level=" UNDER ",
            term=" 1269 ",
            subject=" afm ",
            catalog_num=" 101 ",
        )
    )

    settings = get_settings()
    fake_client = FakeAsyncClient.last_instance

    assert html == "<html><h1>Schedule of Classes</h1></html>"
    assert fake_client is not None
    assert fake_client.post_url == settings.waterloo_schedule_url
    assert fake_client.post_data == {
        "level": "under",
        "sess": "1269",
        "subject": "AFM",
        "cournum": "101",
    }
    assert fake_client.post_headers is not None
    assert "UWSeatWatch/0.1" in fake_client.post_headers["User-Agent"]
    assert settings.contact_email in fake_client.post_headers["User-Agent"]


def test_fetch_course_html_converts_timeout_to_app_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(waterloo_client.httpx, "AsyncClient", TimeoutAsyncClient)

    with pytest.raises(waterloo_client.WaterlooTimeoutError):
        asyncio.run(
            waterloo_client.fetch_course_html(
                level="under",
                term="1269",
                subject="AFM",
                catalog_num="101",
            )
        )


def test_fetch_course_html_converts_http_status_to_app_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(waterloo_client.httpx, "AsyncClient", ErrorStatusAsyncClient)

    with pytest.raises(waterloo_client.WaterlooResponseError):
        asyncio.run(
            waterloo_client.fetch_course_html(
                level="under",
                term="1269",
                subject="AFM",
                catalog_num="101",
            )
        )


def test_fetch_course_html_rejects_unexpected_html(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(waterloo_client.httpx, "AsyncClient", UnexpectedHtmlAsyncClient)

    with pytest.raises(waterloo_client.WaterlooResponseError):
        asyncio.run(
            waterloo_client.fetch_course_html(
                level="under",
                term="1269",
                subject="AFM",
                catalog_num="101",
            )
        )
