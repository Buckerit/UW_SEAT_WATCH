from __future__ import annotations

import httpx
import pytest

from app.config import get_settings
from app.services import email


def clear_settings_cache() -> None:
    get_settings.cache_clear()


def test_console_backend_prints_email(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("EMAIL_BACKEND", "console")
    clear_settings_cache()

    try:
        email.send_email(
            to="student@example.com",
            subject="Verify",
            html="<p>Hello</p>",
            text="Hello",
        )
    finally:
        clear_settings_cache()

    output = capsys.readouterr().out

    assert "UW SEAT WATCH - DEVELOPMENT EMAIL" in output
    assert "To: student@example.com" in output
    assert "Subject: Verify" in output
    assert "Hello" in output


def test_brevo_backend_posts_expected_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: dict[str, object] = {}

    def fake_post(
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, object],
        timeout: float,
    ) -> httpx.Response:
        sent["url"] = url
        sent["headers"] = headers
        sent["json"] = json
        sent["timeout"] = timeout
        request = httpx.Request("POST", url)
        return httpx.Response(201, request=request)

    monkeypatch.setenv("EMAIL_BACKEND", "brevo")
    monkeypatch.setenv("BREVO_API_KEY", "test-key")
    monkeypatch.setenv("FROM_EMAIL", "UW Seat Watch <sender@example.com>")
    monkeypatch.setattr(email.httpx, "post", fake_post)
    clear_settings_cache()

    try:
        email.send_email(
            to="student@example.com",
            subject="Seat open",
            html="<p>Open</p>",
            text="Open",
        )
    finally:
        clear_settings_cache()

    assert sent["url"] == "https://api.brevo.com/v3/smtp/email"
    assert sent["headers"]["api-key"] == "test-key"
    assert sent["json"] == {
        "sender": {
            "name": "UW Seat Watch",
            "email": "sender@example.com",
        },
        "to": [
            {
                "email": "student@example.com",
            }
        ],
        "subject": "Seat open",
        "htmlContent": "<p>Open</p>",
    }
    assert sent["timeout"] == 15.0


def test_brevo_failure_raises_email_delivery_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_post(
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, object],
        timeout: float,
    ) -> httpx.Response:
        request = httpx.Request("POST", url)
        return httpx.Response(401, request=request)

    monkeypatch.setenv("EMAIL_BACKEND", "brevo")
    monkeypatch.setenv("BREVO_API_KEY", "test-key")
    monkeypatch.setattr(email.httpx, "post", fake_post)
    clear_settings_cache()

    try:
        with pytest.raises(email.EmailDeliveryError):
            email.send_email(
                to="student@example.com",
                subject="Seat open",
                html="<p>Open</p>",
                text="Open",
            )
    finally:
        clear_settings_cache()
