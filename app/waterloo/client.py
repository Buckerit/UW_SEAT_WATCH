from __future__ import annotations
import asyncio
import logging
import re
from typing import Any

import httpx

from app.config import get_settings
from app.waterloo.parser import CourseSection


logger = logging.getLogger(__name__)

TERM_OPTION_PATTERN = re.compile(
    r"<option[^>]+value=[\"']?(\d{4})[\"']?",
    re.IGNORECASE,
)


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
openapi_bouncer = asyncio.Semaphore(100)

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
        if exc.response.status_code == 429:
            logger.error(
                "Waterloo salook.pl rate limit hit: HTTP 429 for %s %s term %s",
                payload["subject"],
                payload["cournum"],
                payload["sess"],
            )
        raise WaterlooResponseError(
            f"Waterloo returned HTTP {exc.response.status_code}."
        ) from exc
    except httpx.RequestError as exc:
        raise WaterlooClientError("Waterloo's Schedule of Classes could not be reached.") from exc

    if "Schedule of Classes" not in response.text:
        raise WaterlooResponseError("Waterloo returned an unexpected response.")

    return response.text


async def fetch_published_term_codes() -> set[str]:
    settings = get_settings()

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
                response = await client.get(settings.waterloo_schedule_url, headers=headers)
        response.raise_for_status()
    except httpx.TimeoutException as exc:
        raise WaterlooTimeoutError("Waterloo's Schedule of Classes timed out.") from exc
    except httpx.HTTPStatusError as exc:
        raise WaterlooResponseError(
            f"Waterloo returned HTTP {exc.response.status_code}."
        ) from exc
    except httpx.RequestError as exc:
        raise WaterlooClientError("Waterloo's Schedule of Classes could not be reached.") from exc

    return set(TERM_OPTION_PATTERN.findall(response.text))


def parse_openapi_sections(data: Any) -> list[CourseSection]:
    sections: list[CourseSection] = []

    if not isinstance(data, list):
        raise WaterlooResponseError("Waterloo OpenData returned unexpected JSON.")

    for item in data:
        if not isinstance(item, dict):
            continue

        try:
            class_number = str(item["classNumber"])
            component = str(item["courseComponent"])
            section = str(item["classSection"]).zfill(3)
            capacity = int(item["maxEnrollmentCapacity"])
            enrolled = int(item["enrolledStudents"])
        except (KeyError, TypeError, ValueError):
            continue

        schedule_data = item.get("scheduleData") or []
        first_meeting = (
            schedule_data[0]
            if schedule_data and isinstance(schedule_data[0], dict)
            else {}
        )

        sections.append(
            CourseSection(
                class_number=class_number,
                section_number=f"{component} {section}",
                campus_type=str(first_meeting.get("locationName") or ""),
                associated_class=str(item.get("associatedClassCode") or ""),
                enrollment_capacity=capacity,
                enrollment_total=enrolled,
                waitlist_capacity=0,
                waitlist_total=0,
                meeting_time=str(
                    first_meeting.get("classMeetingDayPatternCode") or ""
                ),
                room=str(first_meeting.get("locationName") or ""),
            )
        )

    return sections


async def fetch_openapi_sections(
    *,
    term: str,
    subject: str,
    catalog_num: str,
) -> list[CourseSection]:
    settings = get_settings()

    if not settings.uw_openapi_key:
        raise WaterlooClientError("UW_OPENAPI_KEY is missing.")

    url = (
        f"{settings.waterloo_openapi_base_url.rstrip('/')}"
        f"/ClassSchedules/{term.strip()}/{subject.strip().upper()}"
        f"/{catalog_num.strip().upper()}"
    )

    headers = {
        "X-API-KEY": settings.uw_openapi_key,
        "User-Agent": (
            f"UWSeatWatch/0.1 (course availability notifier; contact: {settings.contact_email})"
        ),
    }

    try:
        async with openapi_bouncer:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(settings.waterloo_request_timeout_seconds),
                follow_redirects=True,
            ) as client:
                response = await client.get(url, headers=headers)
        response.raise_for_status()
    except httpx.TimeoutException as exc:
        raise WaterlooTimeoutError("Waterloo OpenData timed out.") from exc
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 429:
            logger.error(
                "Waterloo OpenData API rate limit hit: HTTP 429 for %s %s term %s",
                subject.strip().upper(),
                catalog_num.strip().upper(),
                term.strip(),
            )
        raise WaterlooResponseError(
            f"Waterloo OpenData returned HTTP {exc.response.status_code}."
        ) from exc
    except httpx.RequestError as exc:
        raise WaterlooClientError("Waterloo OpenData could not be reached.") from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise WaterlooResponseError(
            "Waterloo OpenData returned invalid JSON."
        ) from exc

    return parse_openapi_sections(data)
