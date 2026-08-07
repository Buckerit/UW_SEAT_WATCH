from __future__ import annotations
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from app.config import get_settings


VERIFY_WATCH_SALT = "verify-watch-v1"
UNSUBSCRIBE_WATCH_SALT = "unsubscribe-watch-v1"
VERIFY_TOKEN_MAX_AGE_SECONDS = 3600  # 1 hour


class VerificationTokenError(ValueError):
    pass


class VerificationTokenExpired(VerificationTokenError):
    pass


def _serializer() -> URLSafeTimedSerializer:
    settings = get_settings()

    return URLSafeTimedSerializer(
        secret_key=settings.secret_key,
    )


def create_watch_verification_token(watch_id: int) -> str:
    return _serializer().dumps(
        {
            "purpose": "verify_watch",
            "watch_id": watch_id,
        },
        salt=VERIFY_WATCH_SALT,
    )


def read_watch_verification_token(token: str) -> int:
    try:
        data = _serializer().loads(
            token,
            salt=VERIFY_WATCH_SALT,
            max_age=VERIFY_TOKEN_MAX_AGE_SECONDS,
        )

    except SignatureExpired as exc:
        raise VerificationTokenExpired(
            "This verification link has expired."
        ) from exc

    except BadSignature as exc:
        raise VerificationTokenError(
            "This verification link is invalid."
        ) from exc

    if data.get("purpose") != "verify_watch":
        raise VerificationTokenError(
            "This verification link has the wrong purpose."
        )

    watch_id = data.get("watch_id")

    if not isinstance(watch_id, int):
        raise VerificationTokenError(
            "This verification link is malformed."
        )

    return watch_id


def create_watch_unsubscribe_token(watch_id: int) -> str:
    return _serializer().dumps(
        {
            "purpose": "unsubscribe_watch",
            "watch_id": watch_id,
        },
        salt=UNSUBSCRIBE_WATCH_SALT,
    )


def read_watch_unsubscribe_token(token: str) -> int:
    try:
        data = _serializer().loads(
            token,
            salt=UNSUBSCRIBE_WATCH_SALT,
        )

    except BadSignature as exc:
        raise VerificationTokenError(
            "This unsubscribe link is invalid."
        ) from exc

    if data.get("purpose") != "unsubscribe_watch":
        raise VerificationTokenError(
            "This link has the wrong purpose."
        )

    watch_id = data.get("watch_id")

    if not isinstance(watch_id, int):
        raise VerificationTokenError(
            "This unsubscribe link is malformed."
        )

    return watch_id
