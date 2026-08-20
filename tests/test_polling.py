from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Notification, SectionState, Subscriber, Watch
from app.services import polling
from app.waterloo.client import WaterlooClientError
from app.waterloo.parser import CourseSection


def create_test_database(tmp_path: Path):
    database_path = (tmp_path / "polling.db").resolve().as_posix()
    engine = create_engine(
        f"sqlite:///{database_path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def enable_sqlite_foreign_keys(
        dbapi_connection: Any,
        connection_record: Any,
    ) -> None:
        del connection_record

        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    TestSessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )
    Base.metadata.create_all(engine)

    return engine, TestSessionLocal


@pytest.fixture()
def polling_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    engine, TestSessionLocal = create_test_database(tmp_path)
    monkeypatch.setattr(polling, "SessionLocal", TestSessionLocal)

    yield TestSessionLocal

    engine.dispose()


def make_section(
    *,
    class_number: str = "3804",
    section_number: str = "LEC 001",
    enrollment_capacity: int = 70,
    enrollment_total: int = 69,
) -> CourseSection:
    return CourseSection(
        class_number=class_number,
        section_number=section_number,
        campus_type="UW U",
        associated_class="1",
        enrollment_capacity=enrollment_capacity,
        enrollment_total=enrollment_total,
        waitlist_capacity=0,
        waitlist_total=0,
        meeting_time="02:30-03:50W",
        room="",
    )


def add_watch(
    db,
    *,
    email: str = "watcher@example.com",
    level: str = "under",
    term: str = "1269",
    subject: str = "AFM",
    catalog_number: str = "101",
    class_number: str = "3804",
    section_name: str = "LEC 001",
) -> int:
    subscriber = Subscriber(
        email=email,
    )
    subscriber.watches.append(
        Watch(
            level=level,
            term=term,
            subject=subject,
            catalog_number=catalog_number,
            class_number=class_number,
            section_name=section_name,
            active=True,
        )
    )
    db.add(subscriber)
    db.commit()
    return subscriber.watches[0].id


def add_state(
    db,
    *,
    level: str = "under",
    term: str = "1269",
    subject: str = "AFM",
    catalog_number: str = "101",
    class_number: str = "3804",
    section_name: str = "LEC 001",
    enrollment_capacity: int = 70,
    enrollment_total: int = 70,
) -> None:
    db.add(
        SectionState(
            level=level,
            term=term,
            subject=subject,
            catalog_number=catalog_number,
            class_number=class_number,
            section_name=section_name,
            enrollment_capacity=enrollment_capacity,
            enrollment_total=enrollment_total,
            waitlist_capacity=0,
            waitlist_total=0,
            last_checked_at=polling.utc_now(),
        )
    )
    db.commit()


async def fake_fetch_course_html(**kwargs: str) -> str:
    return "<html>ok</html>"


def set_sections(
    monkeypatch: pytest.MonkeyPatch,
    sections: list[CourseSection],
) -> None:
    monkeypatch.setattr(polling, "fetch_course_html", fake_fetch_course_html)
    monkeypatch.setattr(
        polling,
        "parse_course_sections",
        lambda html: sections,
    )


def set_openapi_sections(
    monkeypatch: pytest.MonkeyPatch,
    sections: list[CourseSection],
) -> None:
    async def fake_fetch_openapi_sections(**kwargs: str) -> list[CourseSection]:
        return sections

    monkeypatch.setattr(
        polling,
        "fetch_openapi_sections",
        fake_fetch_openapi_sections,
    )


@pytest.mark.anyio
async def test_first_observation_creates_state_without_alert(
    polling_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with polling_db() as db:
        add_watch(db)

    set_sections(monkeypatch, [make_section(enrollment_total=69)])

    summary = await polling.poll_all_watches()

    with polling_db() as db:
        state = db.scalar(select(SectionState))
        notification_count = db.scalar(
            select(func.count()).select_from(Notification)
        )

    assert summary.opening_events == ()
    assert notification_count == 0
    assert state is not None
    assert state.enrollment_capacity == 70
    assert state.enrollment_total == 69


@pytest.mark.anyio
async def test_full_to_open_creates_one_notification_per_active_watch(
    polling_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with polling_db() as db:
        add_watch(db)
        add_state(db, enrollment_total=70, enrollment_capacity=70)

    set_sections(monkeypatch, [make_section(enrollment_total=69)])

    summary = await polling.poll_all_watches()

    with polling_db() as db:
        state = db.scalar(select(SectionState))
        notifications = db.scalars(select(Notification)).all()

    assert len(summary.opening_events) == 1
    assert len(notifications) == 1
    assert notifications[0].kind == "section_open"
    assert state is not None
    assert state.enrollment_total == 69


@pytest.mark.anyio
async def test_openapi_full_to_open_creates_notification(
    polling_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with polling_db() as db:
        add_watch(db)
        add_state(db, enrollment_total=70, enrollment_capacity=70)

    set_openapi_sections(monkeypatch, [make_section(enrollment_total=69)])

    summary = await polling.poll_all_watches_openapi()

    with polling_db() as db:
        notification_count = db.scalar(
            select(func.count()).select_from(Notification)
        )

    assert len(summary.opening_events) == 1
    assert notification_count == 1


@pytest.mark.anyio
async def test_openapi_failure_does_not_affect_salook_check(
    polling_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failing_openapi_fetch(**kwargs: str) -> list[CourseSection]:
        raise WaterlooClientError("rate limited")

    with polling_db() as db:
        add_watch(db)
        add_state(db, enrollment_total=70, enrollment_capacity=70)

    monkeypatch.setattr(
        polling,
        "fetch_openapi_sections",
        failing_openapi_fetch,
    )
    set_sections(monkeypatch, [make_section(enrollment_total=69)])

    openapi_summary = await polling.poll_all_watches_openapi()
    salook_summary = await polling.poll_all_watches()

    with polling_db() as db:
        notification_count = db.scalar(
            select(func.count()).select_from(Notification)
        )

    assert openapi_summary.failed_courses == 1
    assert len(salook_summary.opening_events) == 1
    assert notification_count == 1


@pytest.mark.anyio
async def test_salook_and_openapi_do_not_duplicate_same_opening(
    polling_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with polling_db() as db:
        add_watch(db)
        add_state(db, enrollment_total=70, enrollment_capacity=70)

    open_section = make_section(enrollment_total=69)
    set_sections(monkeypatch, [open_section])
    set_openapi_sections(monkeypatch, [open_section])

    salook_summary = await polling.poll_all_watches()
    openapi_summary = await polling.poll_all_watches_openapi()

    with polling_db() as db:
        notification_count = db.scalar(
            select(func.count()).select_from(Notification)
        )

    assert len(salook_summary.opening_events) == 1
    assert openapi_summary.opening_events == ()
    assert notification_count == 1


@pytest.mark.anyio
async def test_open_to_open_does_not_alert_again(
    polling_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with polling_db() as db:
        add_watch(db)
        add_state(db, enrollment_total=69, enrollment_capacity=70)

    set_sections(monkeypatch, [make_section(enrollment_total=68)])

    summary = await polling.poll_all_watches()

    with polling_db() as db:
        state = db.scalar(select(SectionState))
        notification_count = db.scalar(
            select(func.count()).select_from(Notification)
        )

    assert summary.opening_events == ()
    assert notification_count == 0
    assert state is not None
    assert state.enrollment_total == 68


@pytest.mark.anyio
async def test_open_full_open_can_alert_again(
    polling_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with polling_db() as db:
        add_watch(db)
        add_state(db, enrollment_total=69, enrollment_capacity=70)

    set_sections(monkeypatch, [make_section(enrollment_total=70)])
    first_summary = await polling.poll_all_watches()

    set_sections(monkeypatch, [make_section(enrollment_total=69)])
    second_summary = await polling.poll_all_watches()

    with polling_db() as db:
        notification_count = db.scalar(
            select(func.count()).select_from(Notification)
        )

    assert first_summary.opening_events == ()
    assert len(second_summary.opening_events) == 1
    assert notification_count == 1


@pytest.mark.anyio
async def test_capacity_increase_counts_as_opening(
    polling_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with polling_db() as db:
        add_watch(db)
        add_state(db, enrollment_total=70, enrollment_capacity=70)

    set_sections(
        monkeypatch,
        [make_section(enrollment_total=70, enrollment_capacity=75)],
    )

    summary = await polling.poll_all_watches()

    with polling_db() as db:
        notification_count = db.scalar(
            select(func.count()).select_from(Notification)
        )

    assert len(summary.opening_events) == 1
    assert notification_count == 1


@pytest.mark.anyio
async def test_grouped_course_fetching(
    polling_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []

    async def fake_fetch(**kwargs: str) -> str:
        calls.append((kwargs["subject"], kwargs["catalog_num"]))
        return "<html>ok</html>"

    def fake_parse(html: str) -> list[CourseSection]:
        return [
            make_section(class_number="3804", section_number="LEC 001"),
            make_section(class_number="3234", section_number="LEC 081"),
            make_section(
                class_number="9999",
                section_number="LEC 001",
                enrollment_capacity=100,
                enrollment_total=99,
            ),
        ]

    monkeypatch.setattr(polling, "fetch_course_html", fake_fetch)
    monkeypatch.setattr(polling, "parse_course_sections", fake_parse)

    with polling_db() as db:
        add_watch(db, email="a@example.com")
        add_watch(db, email="b@example.com")
        add_watch(
            db,
            email="c@example.com",
            class_number="3234",
            section_name="LEC 081",
        )

    first_summary = await polling.poll_all_watches()

    with polling_db() as db:
        add_watch(
            db,
            email="d@example.com",
            subject="CS",
            catalog_number="246",
            class_number="9999",
        )

    calls.clear()
    second_summary = await polling.poll_all_watches()

    assert first_summary.distinct_courses == 1
    assert second_summary.distinct_courses == 2
    assert calls.count(("AFM", "101")) == 1
    assert calls.count(("CS", "246")) == 1


@pytest.mark.anyio
async def test_waterloo_failure_is_safe_for_one_course(
    polling_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch(**kwargs: str) -> str:
        if kwargs["subject"] == "AFM":
            raise WaterlooClientError("boom")
        return "<html>ok</html>"

    def fake_parse(html: str) -> list[CourseSection]:
        return [
            make_section(
                class_number="9999",
                section_number="LEC 001",
                enrollment_total=69,
            )
        ]

    monkeypatch.setattr(polling, "fetch_course_html", fake_fetch)
    monkeypatch.setattr(polling, "parse_course_sections", fake_parse)

    with polling_db() as db:
        add_watch(db, email="afm@example.com")
        add_state(db, enrollment_total=70)
        add_watch(
            db,
            email="cs@example.com",
            subject="CS",
            catalog_number="246",
            class_number="9999",
        )
        add_state(
            db,
            subject="CS",
            catalog_number="246",
            class_number="9999",
            enrollment_total=70,
        )

    summary = await polling.poll_all_watches()

    with polling_db() as db:
        afm_state = db.scalar(
            select(SectionState).where(SectionState.subject == "AFM")
        )
        notification_count = db.scalar(
            select(func.count()).select_from(Notification)
        )

    assert summary.failed_courses == 1
    assert summary.successful_courses == 1
    assert len(summary.opening_events) == 1
    assert notification_count == 1
    assert afm_state is not None
    assert afm_state.enrollment_total == 70
    assert afm_state.enrollment_capacity == 70


@pytest.mark.anyio
async def test_unexpected_parser_failure_is_safe_for_one_course(
    polling_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch(**kwargs: str) -> str:
        return kwargs["subject"]

    def fake_parse(html: str) -> list[CourseSection]:
        if html == "AFM":
            raise RuntimeError("parser exploded")

        return [
            make_section(
                class_number="9999",
                section_number="LEC 001",
                enrollment_total=69,
            )
        ]

    monkeypatch.setattr(polling, "fetch_course_html", fake_fetch)
    monkeypatch.setattr(polling, "parse_course_sections", fake_parse)

    with polling_db() as db:
        add_watch(db, email="afm@example.com")
        add_state(db, enrollment_total=70, enrollment_capacity=70)
        add_watch(
            db,
            email="cs@example.com",
            subject="CS",
            catalog_number="246",
            class_number="9999",
        )
        add_state(
            db,
            subject="CS",
            catalog_number="246",
            class_number="9999",
            enrollment_total=70,
            enrollment_capacity=70,
        )

    summary = await polling.poll_all_watches()

    with polling_db() as db:
        afm_state = db.scalar(
            select(SectionState).where(SectionState.subject == "AFM")
        )
        notifications = db.scalars(select(Notification)).all()

    assert summary.failed_courses == 1
    assert summary.successful_courses == 1
    assert len(summary.opening_events) == 1
    assert afm_state is not None
    assert afm_state.enrollment_total == 70
    assert afm_state.enrollment_capacity == 70
    assert len(notifications) == 1
    assert json.loads(notifications[0].payload)["subject"] == "CS"


@pytest.mark.anyio
async def test_unexpected_processing_failure_does_not_update_or_alert(
    polling_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch(**kwargs: str) -> str:
        return kwargs["subject"]

    def bad_section() -> CourseSection:
        return CourseSection(
            class_number="3804",
            section_number="LEC 001",
            campus_type="UW U",
            associated_class="1",
            enrollment_capacity="bad",  # type: ignore[arg-type]
            enrollment_total=69,
            waitlist_capacity=0,
            waitlist_total=0,
            meeting_time="02:30-03:50W",
            room="",
        )

    def fake_parse(html: str) -> list[CourseSection]:
        if html == "AFM":
            return [bad_section()]

        return [
            make_section(
                class_number="9999",
                section_number="LEC 001",
                enrollment_total=69,
            )
        ]

    monkeypatch.setattr(polling, "fetch_course_html", fake_fetch)
    monkeypatch.setattr(polling, "parse_course_sections", fake_parse)

    with polling_db() as db:
        add_watch(db, email="afm@example.com")
        add_state(db, enrollment_total=70, enrollment_capacity=70)
        add_watch(
            db,
            email="cs@example.com",
            subject="CS",
            catalog_number="246",
            class_number="9999",
        )
        add_state(
            db,
            subject="CS",
            catalog_number="246",
            class_number="9999",
            enrollment_total=70,
            enrollment_capacity=70,
        )

    summary = await polling.poll_all_watches()

    with polling_db() as db:
        afm_state = db.scalar(
            select(SectionState).where(SectionState.subject == "AFM")
        )
        notifications = db.scalars(select(Notification)).all()

    assert summary.failed_courses == 1
    assert summary.successful_courses == 1
    assert len(summary.opening_events) == 1
    assert afm_state is not None
    assert afm_state.enrollment_total == 70
    assert afm_state.enrollment_capacity == 70
    assert len(notifications) == 1
    assert json.loads(notifications[0].payload)["subject"] == "CS"


@pytest.mark.anyio
async def test_missing_section_is_safe(
    polling_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with polling_db() as db:
        add_watch(db, email="missing@example.com")
        add_state(db, enrollment_total=70)
        add_watch(
            db,
            email="present@example.com",
            class_number="3234",
            section_name="LEC 081",
        )
        add_state(
            db,
            class_number="3234",
            section_name="LEC 081",
            enrollment_total=70,
        )

    set_sections(
        monkeypatch,
        [
            make_section(
                class_number="3234",
                section_number="LEC 081",
                enrollment_total=69,
            )
        ],
    )

    summary = await polling.poll_all_watches()

    with polling_db() as db:
        missing_state = db.scalar(
            select(SectionState).where(
                SectionState.class_number == "3804"
            )
        )
        notification_count = db.scalar(
            select(func.count()).select_from(Notification)
        )

    assert summary.missing_sections == 1
    assert len(summary.opening_events) == 1
    assert notification_count == 1
    assert missing_state is not None
    assert missing_state.enrollment_total == 70
