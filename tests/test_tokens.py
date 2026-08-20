from __future__ import annotations

import pytest

from app.services import tokens


def test_verification_token_round_trip() -> None:
    token = tokens.create_watch_verification_token(123)

    assert tokens.read_watch_verification_token(token) == 123


def test_modified_verification_token_is_invalid() -> None:
    token = tokens.create_watch_verification_token(123) + "x"

    with pytest.raises(tokens.VerificationTokenError):
        tokens.read_watch_verification_token(token)


def test_expired_verification_token_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = tokens.create_watch_verification_token(123)
    monkeypatch.setattr(tokens, "VERIFY_TOKEN_MAX_AGE_SECONDS", -1)

    with pytest.raises(tokens.VerificationTokenExpired):
        tokens.read_watch_verification_token(token)


def test_wrong_purpose_verification_token_is_rejected() -> None:
    token = tokens._serializer().dumps(
        {
            "purpose": "unsubscribe_watch",
            "watch_id": 123,
        },
        salt=tokens.VERIFY_WATCH_SALT,
    )

    with pytest.raises(tokens.VerificationTokenError):
        tokens.read_watch_verification_token(token)


def test_unsubscribe_token_round_trip() -> None:
    token = tokens.create_watch_unsubscribe_token(456)

    assert tokens.read_watch_unsubscribe_token(token) == 456


def test_manage_watches_token_round_trip() -> None:
    token = tokens.create_manage_watches_token(
        subscriber_id=7,
        email="student@example.com",
    )

    assert tokens.read_manage_watches_token(token) == (
        7,
        "student@example.com",
    )


def test_modified_manage_watches_token_is_invalid() -> None:
    token = tokens.create_manage_watches_token(
        subscriber_id=7,
        email="student@example.com",
    )

    with pytest.raises(tokens.VerificationTokenError):
        tokens.read_manage_watches_token(token + "x")


def test_expired_manage_watches_token_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = tokens.create_manage_watches_token(
        subscriber_id=7,
        email="student@example.com",
    )
    monkeypatch.setattr(tokens, "MANAGE_TOKEN_MAX_AGE_SECONDS", -1)

    with pytest.raises(tokens.VerificationTokenExpired):
        tokens.read_manage_watches_token(token)


def test_manage_request_token_round_trip() -> None:
    token = tokens.create_manage_request_token("student@example.com")

    assert tokens.read_manage_request_token(token) == "student@example.com"


def test_modified_manage_request_token_is_invalid() -> None:
    token = tokens.create_manage_request_token("student@example.com")

    with pytest.raises(tokens.VerificationTokenError):
        tokens.read_manage_request_token(token + "x")
