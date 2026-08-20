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
