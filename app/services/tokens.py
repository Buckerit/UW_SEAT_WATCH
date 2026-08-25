from __future__ import annotations
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from app.config import get_settings


VERIFY_WATCH_SALT = "verify-watch-v1"
UNSUBSCRIBE_WATCH_SALT = "unsubscribe-watch-v1"
WATCH_STATUS_PRG_SALT = "watch-status-v1"
MANAGE_PRG_SALT = "manage-prg-v1"
RESEND_VERIFICATION_PRG_SALT = "resend-verification-prg-v1"
MANAGE_WATCHES_SALT = "manage-watches-v1"
MANAGE_REQUEST_SALT = "manage-request-v1"
VERIFY_TOKEN_MAX_AGE_SECONDS = 3600  # 1 hour
MANAGE_TOKEN_MAX_AGE_SECONDS = 600  # 10 minutes


class VerificationTokenError(ValueError):
    pass


class VerificationTokenExpired(VerificationTokenError):
    pass


def _serializer() -> URLSafeTimedSerializer:
    settings = get_settings()

    return URLSafeTimedSerializer(
        secret_key=settings.secret_key,
    )


def _prg_serializer() -> URLSafeTimedSerializer:
    settings = get_settings()

    return URLSafeTimedSerializer(
        secret_key=settings.prg_secret_key,
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


def create_manage_watches_token(*, subscriber_id: int, email: str) -> str:
    return _serializer().dumps(
        {
            "purpose": "manage_watches",
            "subscriber_id": subscriber_id,
            "email": email,
        },
        salt=MANAGE_WATCHES_SALT,
    )


def read_manage_watches_token(token: str) -> tuple[int, str]:
    try:
        data = _serializer().loads(
            token,
            salt=MANAGE_WATCHES_SALT,
            max_age=MANAGE_TOKEN_MAX_AGE_SECONDS,
        )

    except SignatureExpired as exc:
        raise VerificationTokenExpired(
            "This management link has expired."
        ) from exc

    except BadSignature as exc:
        raise VerificationTokenError(
            "This management link is invalid."
        ) from exc

    if data.get("purpose") != "manage_watches":
        raise VerificationTokenError(
            "This management link has the wrong purpose."
        )

    subscriber_id = data.get("subscriber_id")
    email = data.get("email")

    if not isinstance(subscriber_id, int) or not isinstance(email, str):
        raise VerificationTokenError(
            "This management link is malformed."
        )

    return subscriber_id, email


def create_manage_request_token(email: str) -> str:
    return _serializer().dumps(
        {
            "purpose": "manage_request",
            "email": email,
        },
        salt=MANAGE_REQUEST_SALT,
    )


def read_manage_request_token(token: str) -> str:
    try:
        data = _serializer().loads(
            token,
            salt=MANAGE_REQUEST_SALT,
            max_age=MANAGE_TOKEN_MAX_AGE_SECONDS,
        )

    except SignatureExpired as exc:
        raise VerificationTokenExpired(
            "This resend link has expired."
        ) from exc

    except BadSignature as exc:
        raise VerificationTokenError(
            "This resend link is invalid."
        ) from exc

    if data.get("purpose") != "manage_request":
        raise VerificationTokenError(
            "This resend link has the wrong purpose."
        )

    email = data.get("email")

    if not isinstance(email, str):
        raise VerificationTokenError(
            "This resend link is malformed."
        )

    return email


def make_watch_status_token(
    watch_id: int,
    *,
    message: str = (
        "The watch was saved. It is currently pending email verification."
    ),
    resend_message: str | None = None,
    status_code: int = 200,
) -> str:
    return _prg_serializer().dumps(
        {
            "purpose": "watch_status",
            "watch_id": watch_id,
            "message": message,
            "resend_message": resend_message,
            "status_code": status_code,
        },
        salt=WATCH_STATUS_PRG_SALT,
    )


def read_watch_status_token(token: str) -> dict[str, object]:
    try:
        data = _prg_serializer().loads(token, salt=WATCH_STATUS_PRG_SALT)
    except BadSignature as exc:
        raise VerificationTokenError("This watch status link is invalid.") from exc

    if data.get("purpose") != "watch_status":
        raise VerificationTokenError("This watch status link has the wrong purpose.")

    watch_id = data.get("watch_id")

    if not isinstance(watch_id, int):
        raise VerificationTokenError(
            "This watch status link is malformed."
        )

    return data


def create_manage_prg_token(
    *,
    resend_token: str,
    resend_message: str | None = None,
    status_code: int = 200,
) -> str:
    return _prg_serializer().dumps(
        {
            "purpose": "manage_prg",
            "resend_token": resend_token,
            "resend_message": resend_message,
            "status_code": status_code,
        },
        salt=MANAGE_PRG_SALT,
    )


def read_manage_prg_token(token: str) -> dict[str, object]:
    try:
        data = _prg_serializer().loads(token, salt=MANAGE_PRG_SALT)
    except BadSignature as exc:
        raise VerificationTokenError("This manage status link is invalid.") from exc

    if data.get("purpose") != "manage_prg":
        raise VerificationTokenError("This manage status link has the wrong purpose.")

    resend_token = data.get("resend_token")

    if not isinstance(resend_token, str):
        raise VerificationTokenError(
            "This manage status link is malformed."
        )

    return data


def create_resend_verification_prg_token(
    *,
    watch_id: int,
    message: str,
    resend_message: str | None = None,
    status_code: int = 200,
) -> str:
    return _prg_serializer().dumps(
        {
            "purpose": "resend_verification_prg",
            "watch_id": watch_id,
            "message": message,
            "resend_message": resend_message,
            "status_code": status_code,
        },
        salt=RESEND_VERIFICATION_PRG_SALT,
    )


def read_resend_verification_prg_token(token: str) -> dict[str, object]:
    try:
        data = _prg_serializer().loads(
            token,
            salt=RESEND_VERIFICATION_PRG_SALT,
        )
    except BadSignature as exc:
        raise VerificationTokenError(
            "This verification status link is invalid."
        ) from exc

    if data.get("purpose") != "resend_verification_prg":
        raise VerificationTokenError(
            "This verification status link has the wrong purpose."
        )

    watch_id = data.get("watch_id")

    if not isinstance(watch_id, int):
        raise VerificationTokenError(
            "This verification status link is malformed."
        )

    return data
    
