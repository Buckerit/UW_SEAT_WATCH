from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.database import SessionLocal
from app.models import Notification, SectionState, Watch, utc_now
from app.waterloo.client import (
    WaterlooClientError,
    fetch_course_html,
    fetch_openapi_sections,
)
from app.waterloo.parser import parse_course_sections


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CourseKey:
    level: str
    term: str
    subject: str
    catalog_number: str


@dataclass(frozen=True)
class WatchTarget:
    watch_id: int
    email: str
    class_number: str
    section_name: str


@dataclass(frozen=True)
class OpeningEvent:
    watch_id: int
    email: str
    term: str
    subject: str
    catalog_number: str
    class_number: str
    section_name: str
    previous_capacity: int
    previous_enrollment: int
    current_capacity: int
    current_enrollment: int

    @property
    def available_seats(self) -> int:
        return max(
            self.current_capacity - self.current_enrollment,
            0,
        )


@dataclass(frozen=True)
class PollSummary:
    active_watches: int
    distinct_courses: int
    successful_courses: int
    failed_courses: int
    missing_sections: int
    opening_events: tuple[OpeningEvent, ...]


def _load_active_watches() -> dict[CourseKey, list[WatchTarget]]:
    grouped: dict[CourseKey, list[WatchTarget]] = defaultdict(list)

    with SessionLocal() as db:
        watches = db.scalars(
            select(Watch)
            .options(joinedload(Watch.subscriber))
            .where(Watch.active.is_(True))
        ).all()

        for watch in watches:
            course_key = CourseKey(
                level=watch.level,
                term=watch.term,
                subject=watch.subject,
                catalog_number=watch.catalog_number,
            )

            grouped[course_key].append(
                WatchTarget(
                    watch_id=watch.id,
                    email=watch.subscriber.email,
                    class_number=watch.class_number,
                    section_name=watch.section_name,
                )
            )

    return grouped


async def _poll_grouped_watches(
    grouped_watches: dict[CourseKey, list[WatchTarget]],
    *,
    source: str,
) -> PollSummary:
    active_watch_count = sum(
        len(watches)
        for watches in grouped_watches.values()
    )

    opening_events: list[OpeningEvent] = []

    successful_courses = 0
    failed_courses = 0
    missing_sections = 0

    logger.info(
        "Starting %s polling cycle: %s active watches across %s courses",
        source,
        active_watch_count,
        len(grouped_watches),
    )

    for course, targets in grouped_watches.items():
        try:
            if source == "openapi":
                parsed_sections = await fetch_openapi_sections(
                    term=course.term,
                    subject=course.subject,
                    catalog_num=course.catalog_number,
                )
            else:
                html = await fetch_course_html(
                    level=course.level,
                    term=course.term,
                    subject=course.subject,
                    catalog_num=course.catalog_number,
                )
                parsed_sections = parse_course_sections(html)

        except WaterlooClientError:
            failed_courses += 1

            logger.exception(
                "Could not fetch %s %s for term %s from %s",
                course.subject,
                course.catalog_number,
                course.term,
                source,
            )

            continue

        successful_courses += 1

        sections_by_class_number = {
            section.class_number: section
            for section in parsed_sections
        }

        targets_by_class_number: dict[
            str,
            list[WatchTarget],
        ] = defaultdict(list)

        for target in targets:
            targets_by_class_number[
                target.class_number
            ].append(target)

        checked_at = utc_now()

        with SessionLocal() as db:
            for class_number, section_targets in (
                targets_by_class_number.items()
            ):
                current_section = sections_by_class_number.get(
                    class_number
                )

                if current_section is None:
                    missing_sections += 1

                    logger.warning(
                        (
                            "Watched section missing from Waterloo "
                            "response: term=%s course=%s %s class=%s"
                        ),
                        course.term,
                        course.subject,
                        course.catalog_number,
                        class_number,
                    )

                    continue

                previous_state = db.scalar(
                    select(SectionState).where(
                        SectionState.term == course.term,
                        SectionState.class_number
                        == class_number,
                    )
                )

                if previous_state is None:
                    db.add(
                        SectionState(
                            level=course.level,
                            term=course.term,
                            subject=course.subject,
                            catalog_number=course.catalog_number,
                            class_number=class_number,
                            section_name=current_section.section_number,
                            enrollment_capacity=(
                                current_section.enrollment_capacity
                            ),
                            enrollment_total=(
                                current_section.enrollment_total
                            ),
                            waitlist_capacity=(
                                current_section.waitlist_capacity
                            ),
                            waitlist_total=(
                                current_section.waitlist_total
                            ),
                            last_checked_at=checked_at,
                        )
                    )

                    logger.info(
                        (
                            "Stored first observation for "
                            "%s %s %s: %s/%s"
                        ),
                        course.subject,
                        course.catalog_number,
                        current_section.section_number,
                        current_section.enrollment_total,
                        current_section.enrollment_capacity,
                    )

                    # First observation establishes the baseline.
                    # It must never produce an alert.
                    continue

                was_full = (
                    previous_state.enrollment_total
                    >= previous_state.enrollment_capacity
                )

                is_now_open = (
                    current_section.enrollment_total
                    < current_section.enrollment_capacity
                )

                previous_capacity = (
                    previous_state.enrollment_capacity
                )
                previous_enrollment = (
                    previous_state.enrollment_total
                )

                previous_state.level = course.level
                previous_state.subject = course.subject
                previous_state.catalog_number = (
                    course.catalog_number
                )
                previous_state.section_name = (
                    current_section.section_number
                )
                previous_state.enrollment_capacity = (
                    current_section.enrollment_capacity
                )
                previous_state.enrollment_total = (
                    current_section.enrollment_total
                )
                previous_state.waitlist_capacity = (
                    current_section.waitlist_capacity
                )
                previous_state.waitlist_total = (
                    current_section.waitlist_total
                )
                previous_state.last_checked_at = checked_at

                if not is_now_open:
                    for target in section_targets:
                        watch = db.get(Watch, target.watch_id)
                        if watch is not None:
                            watch.last_seen_open = False

                if was_full and is_now_open:
                    for target in section_targets:
                        payload = {
                            "email": target.email,
                            "term": course.term,
                            "subject": course.subject,
                            "catalog_number": course.catalog_number,
                            "class_number": class_number,
                            "section_name": current_section.section_number,
                            "previous_capacity": previous_capacity,
                            "previous_enrollment": previous_enrollment,
                            "current_capacity": (
                                current_section.enrollment_capacity
                            ),
                            "current_enrollment": (
                                current_section.enrollment_total
                            ),
                        }

                        db.add(
                            Notification(
                                watch_id=target.watch_id,
                                kind="section_open",
                                payload=json.dumps(payload),
                            )
                        )

                        watch = db.get(Watch, target.watch_id)
                        if watch is not None:
                            watch.last_seen_open = True

                        opening_events.append(
                            OpeningEvent(
                                watch_id=target.watch_id,
                                email=target.email,
                                term=course.term,
                                subject=course.subject,
                                catalog_number=(
                                    course.catalog_number
                                ),
                                class_number=class_number,
                                section_name=(
                                    current_section.section_number
                                ),
                                previous_capacity=(
                                    previous_capacity
                                ),
                                previous_enrollment=(
                                    previous_enrollment
                                ),
                                current_capacity=(
                                    current_section
                                    .enrollment_capacity
                                ),
                                current_enrollment=(
                                    current_section
                                    .enrollment_total
                                ),
                            )
                        )

                    logger.info(
                        (
                            "%s opening detected for %s %s %s: "
                            "%s/%s -> %s/%s"
                        ),
                        source,
                        course.subject,
                        course.catalog_number,
                        current_section.section_number,
                        previous_enrollment,
                        previous_capacity,
                        current_section.enrollment_total,
                        current_section.enrollment_capacity,
                    )

            db.commit()

    summary = PollSummary(
        active_watches=active_watch_count,
        distinct_courses=len(grouped_watches),
        successful_courses=successful_courses,
        failed_courses=failed_courses,
        missing_sections=missing_sections,
        opening_events=tuple(opening_events),
    )

    logger.info(
        (
            "%s polling complete: successful=%s failed=%s "
            "missing_sections=%s opening_events=%s"
        ),
        source,
        summary.successful_courses,
        summary.failed_courses,
        summary.missing_sections,
        len(summary.opening_events),
    )

    return summary


async def poll_all_watches() -> PollSummary:
    return await _poll_grouped_watches(
        _load_active_watches(),
        source="salook",
    )


async def poll_all_watches_openapi() -> PollSummary:
    return await _poll_grouped_watches(
        _load_active_watches(),
        source="openapi",
    )
