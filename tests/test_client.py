import asyncio
import logging

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


class RateLimitAsyncClient:
    def __init__(self, *, timeout: httpx.Timeout, follow_redirects: bool) -> None:
        self.timeout = timeout
        self.follow_redirects = follow_redirects

    async def __aenter__(self) -> "RateLimitAsyncClient":
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
        return httpx.Response(429, request=request, text="too many requests")

    async def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
    ) -> httpx.Response:
        request = httpx.Request("GET", url)
        return httpx.Response(429, request=request, text="too many requests")


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


class InvalidJsonAsyncClient:
    def __init__(self, *, timeout: httpx.Timeout, follow_redirects: bool) -> None:
        self.timeout = timeout
        self.follow_redirects = follow_redirects

    async def __aenter__(self) -> "InvalidJsonAsyncClient":
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    async def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
    ) -> httpx.Response:
        request = httpx.Request("GET", url)
        return httpx.Response(200, request=request, text="not json")


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


def test_fetch_course_html_logs_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(waterloo_client.httpx, "AsyncClient", RateLimitAsyncClient)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(waterloo_client.WaterlooResponseError):
            asyncio.run(
                waterloo_client.fetch_course_html(
                    level="under",
                    term="1269",
                    subject="AFM",
                    catalog_num="101",
                )
            )

    assert "salook.pl rate limit hit" in caplog.text


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


def test_parse_openapi_sections_converts_class_schedule_items() -> None:
    sections = waterloo_client.parse_openapi_sections(
        [
            {
                "classNumber": 6016,
                "courseComponent": "LEC",
                "classSection": 1,
                "associatedClassCode": 0,
                "maxEnrollmentCapacity": 250,
                "enrolledStudents": 232,
                "scheduleData": [
                    {
                        "classMeetingDayPatternCode": "MWF",
                        "locationName": "UW U",
                    }
                ],
            }
        ]
    )

    assert len(sections) == 1
    assert sections[0].class_number == "6016"
    assert sections[0].section_number == "LEC 001"
    assert sections[0].enrollment_capacity == 250
    assert sections[0].enrollment_total == 232
    assert sections[0].appears_open is True


def test_fetch_openapi_sections_logs_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(waterloo_client.httpx, "AsyncClient", RateLimitAsyncClient)
    monkeypatch.setenv("UW_OPENAPI_KEY", "test-key")
    waterloo_client.get_settings.cache_clear()

    try:
        with caplog.at_level(logging.ERROR):
            with pytest.raises(waterloo_client.WaterlooResponseError):
                asyncio.run(
                    waterloo_client.fetch_openapi_sections(
                        term="1269",
                        subject="STAT",
                        catalog_num="230",
                    )
                )

        assert "OpenData API rate limit hit" in caplog.text
    finally:
        waterloo_client.get_settings.cache_clear()


def test_fetch_openapi_sections_wraps_invalid_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(waterloo_client.httpx, "AsyncClient", InvalidJsonAsyncClient)
    monkeypatch.setenv("UW_OPENAPI_KEY", "test-key")
    waterloo_client.get_settings.cache_clear()

    try:
        with pytest.raises(waterloo_client.WaterlooResponseError):
            asyncio.run(
                waterloo_client.fetch_openapi_sections(
                    term="1269",
                    subject="STAT",
                    catalog_num="230",
                )
            )
    finally:
        waterloo_client.get_settings.cache_clear()
