from __future__ import annotations

import logging
from typing import Any


logger = logging.getLogger("uwseatwatch.events")


def mask_email(email: str) -> str:
    local, separator, domain = email.partition("@")

    if not separator:
        return email

    if len(local) <= 1:
        return f"*@{domain}"

    return f"{local[0]}***@{domain}"


def log_event(event_type: str, action: str, **fields: Any) -> None:
    parts = [
        f"EVENT_{event_type.upper()}",
        f"action={action}",
    ]

    for key, value in fields.items():
        if value is None:
            continue

        safe_value = str(value).replace("\n", " ").replace("\r", " ")
        parts.append(f"{key}={safe_value}")

    logger.info(" ".join(parts))
