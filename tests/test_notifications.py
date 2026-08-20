from __future__ import annotations

from app.services import notifications


def test_verification_email_includes_unsubscribe_link(
    monkeypatch,
) -> None:
    sent: dict[str, str] = {}

    def fake_send_email(
        *,
        to: str,
        subject: str,
        html: str,
        text: str,
    ) -> None:
        sent["to"] = to
        sent["subject"] = subject
        sent["html"] = html
        sent["text"] = text

    monkeypatch.setattr(
        notifications,
        "send_email",
        fake_send_email,
    )

    verification_url = notifications.send_watch_verification_email(
        watch_id=123,
        email="student@example.com",
        subject="STAT",
        catalog_number="230",
        section_name="LEC 081",
    )

    assert "/verify?token=" in verification_url
    assert "/unsubscribe?token=" in sent["text"]
    assert "/unsubscribe?token=" in sent["html"]
    assert "To cancel this watch" in sent["text"]
    assert "Cancel this watch" in sent["html"]
