from __future__ import annotations
import asyncio

import httpx

from app.config import get_settings


class WaterlooClientError(RuntimeError):
    """Base error for failures while fetching Waterloo course HTML."""

    pass


class WaterlooTimeoutError(WaterlooClientError):
    """Raised when Waterloo does not respond within our configured timeout."""

    pass


class WaterlooResponseError(WaterlooClientError):
    """Raised when Waterloo responds, but not with usable Schedule of Classes HTML."""

    pass

waterloo_bouncer = asyncio.Semaphore(2)

async def fetch_course_html(*, level: str, term: str, subject: str, catalog_num: str) -> str:
    settings = get_settings()

    # Waterloo expects a normal HTML form POST, so httpx sends this as form data.
    payload = {
        "level": level.strip().lower(),
        "sess": term.strip(),
        "subject": subject.strip().upper(),
        "cournum": catalog_num.strip().upper(),
    }
    headers = {
        "User-Agent": (
            f"UWSeatWatch/0.1 (course availability notifier; contact: {settings.contact_email})"
        )
    }

    try:
        async with waterloo_bouncer:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(settings.waterloo_request_timeout_seconds),
                follow_redirects=True,
            ) as client:
                response = await client.post(settings.waterloo_schedule_url, data=payload, headers=headers)
        response.raise_for_status()
    except httpx.TimeoutException as exc:
        raise WaterlooTimeoutError("Waterloo's Schedule of Classes timed out.") from exc
    except httpx.HTTPStatusError as exc:
        raise WaterlooResponseError(
            f"Waterloo returned HTTP {exc.response.status_code}."
        ) from exc
    except httpx.RequestError as exc:
        raise WaterlooClientError("Waterloo's Schedule of Classes could not be reached.") from exc

    if "Schedule of Classes" not in response.text:
        raise WaterlooResponseError("Waterloo returned an unexpected response.")

    return response.text
