from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models import Notification, SectionState, Subscriber, Watch
from app.routes import watches as watch_routes
from app.config import get_settings
from app.services.tokens import (
    create_manage_watches_token,
    create_watch_unsubscribe_token,
    create_watch_verification_token,
)
from app.waterloo.parser import CourseSection


def manage_resend_token(response) -> str:
    match = re.search(
        r'name="resend_token"\s+value="([^"]+)"',
        response.text,
    )
    assert match is not None
    return match.group(1)


def create_test_database(tmp_path: Path):
    database_path = (tmp_path / "routes.db").resolve().as_posix()
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
def route_db(tmp_path: Path):
    engine, TestSessionLocal = create_test_database(tmp_path)
    watch_routes.manage_link_requests.clear()

    def override_get_db():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    yield TestSessionLocal

    app.dependency_overrides.clear()
    watch_routes.manage_link_requests.clear()
    engine.dispose()


def add_watch(
    route_db,
    *,
    email: str = "route@example.com",
    active: bool = False,
    confirmed: bool = False,
    last_seen_open: bool = False,
    class_number: str = "3804",
    section_name: str = "LEC 001",
) -> int:
    with route_db() as db:
        subscriber = Subscriber(email=email)
        subscriber.watches.append(
            Watch(
                level="under",
                term="1269",
                subject="AFM",
                catalog_number="101",
                class_number=class_number,
                section_name=section_name,
                active=active,
                last_seen_open=last_seen_open,
                confirmed_at=(
                    datetime.now(timezone.utc)
                    if confirmed
                    else None
                ),
            )
        )
        db.add(subscriber)
        db.commit()
        return subscriber.watches[0].id


def test_verification_route_is_idempotent(route_db) -> None:
    watch_id = add_watch(route_db)
    token = create_watch_verification_token(watch_id)
    client = TestClient(app)

    first_response = client.get(f"/verify?token={token}")
    second_response = client.get(f"/verify?token={token}")

    with route_db() as db:
        watch = db.get(Watch, watch_id)

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert watch is not None
    assert watch.active is True
    assert watch.confirmed_at is not None


def test_verification_queues_alert_for_already_open_watch(route_db) -> None:
    watch_id = add_watch(route_db, last_seen_open=True)

    with route_db() as db:
        db.add(
            SectionState(
                level="under",
                term="1269",
                subject="AFM",
                catalog_number="101",
                class_number="3804",
                section_name="LEC 001",
                enrollment_capacity=70,
                enrollment_total=69,
                waitlist_capacity=0,
                waitlist_total=0,
                last_checked_at=datetime.now(timezone.utc),
            )
        )
        db.commit()

    token = create_watch_verification_token(watch_id)
    client = TestClient(app)

    response = client.get(f"/verify?token={token}")

    with route_db() as db:
        notification_count = db.scalar(
            select(func.count()).select_from(Notification)
        )

    assert response.status_code == 200
    assert notification_count == 1


def test_verification_immediately_attempts_open_alert_send(
    route_db,
    monkeypatch: pytest.MonkeyPatch,
    ) -> None:
    watch_id = add_watch(route_db, last_seen_open=True)
    sent_watch_ids: list[int | None] = []

    with route_db() as db:
        db.add(
            SectionState(
                level="under",
                term="1269",
                subject="AFM",
                catalog_number="101",
                class_number="3804",
                section_name="LEC 001",
                enrollment_capacity=70,
                enrollment_total=69,
                waitlist_capacity=0,
                waitlist_total=0,
                last_checked_at=datetime.now(timezone.utc),
            )
        )
        db.commit()

    def fake_send_pending_notifications(
        watch_id: int | None = None,
    ) -> int:
        sent_watch_ids.append(watch_id)
        return 1

    monkeypatch.setattr(
        watch_routes,
        "send_pending_notifications",
        fake_send_pending_notifications,
    )

    token = create_watch_verification_token(watch_id)
    client = TestClient(app)

    response = client.get(f"/verify?token={token}")

    assert response.status_code == 200
    assert sent_watch_ids == [watch_id]


def test_existing_inactive_watch_resends_verification_email(
    route_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    watch_id = add_watch(route_db, active=False, confirmed=False)
    sent_watch_ids: list[int] = []

    async def fake_fetch_course_html(**kwargs: str) -> str:
        return "<html>ok</html>"

    def fake_parse_course_sections(html: str) -> list[CourseSection]:
        return [
            CourseSection(
                class_number="3804",
                section_number="LEC 001",
                campus_type="UW U",
                associated_class="1",
                enrollment_capacity=70,
                enrollment_total=70,
                waitlist_capacity=0,
                waitlist_total=0,
                meeting_time="",
                room="",
            )
        ]

    def fake_send_watch_verification_email(**kwargs: object) -> str:
        sent_watch_ids.append(int(kwargs["watch_id"]))
        return "https://example.com/verify"

    monkeypatch.setattr(
        watch_routes,
        "fetch_course_html",
        fake_fetch_course_html,
    )
    monkeypatch.setattr(
        watch_routes,
        "parse_course_sections",
        fake_parse_course_sections,
    )
    monkeypatch.setattr(
        watch_routes,
        "send_watch_verification_email",
        fake_send_watch_verification_email,
    )

    client = TestClient(app)

    response = client.post(
        "/watches",
        data={
            "level": "under",
            "term": "1269",
            "subject": "AFM",
            "catalog_number": "101",
            "class_number": "3804",
            "email": "route@example.com",
        },
    )

    assert response.status_code == 200
    assert sent_watch_ids == [watch_id]
    assert "fresh verification email" in response.text


def test_production_watch_creation_does_not_show_verification_link(
    route_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch_course_html(**kwargs: str) -> str:
        return "<html>ok</html>"

    def fake_parse_course_sections(html: str) -> list[CourseSection]:
        return [
            CourseSection(
                class_number="3804",
                section_number="LEC 001",
                campus_type="UW U",
                associated_class="1",
                enrollment_capacity=70,
                enrollment_total=70,
                waitlist_capacity=0,
                waitlist_total=0,
                meeting_time="",
                room="",
            )
        ]

    def fake_send_watch_verification_email(**kwargs: object) -> str:
        return "https://example.com/verify?token=secret"

    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()
    monkeypatch.setattr(
        watch_routes,
        "fetch_course_html",
        fake_fetch_course_html,
    )
    monkeypatch.setattr(
        watch_routes,
        "parse_course_sections",
        fake_parse_course_sections,
    )
    monkeypatch.setattr(
        watch_routes,
        "send_watch_verification_email",
        fake_send_watch_verification_email,
    )

    try:
        client = TestClient(app)

        response = client.post(
            "/watches",
            data={
                "level": "under",
                "term": "1269",
                "subject": "AFM",
                "catalog_number": "101",
                "class_number": "3804",
                "email": "route@example.com",
            },
        )
    finally:
        get_settings.cache_clear()

    assert response.status_code == 201
    assert "Development verification link" not in response.text
    assert "verify?token=secret" not in response.text


def test_pending_watch_can_resend_verification_email(
    route_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    watch_id = add_watch(route_db, active=False, confirmed=False)
    sent_watch_ids: list[int] = []

    with route_db() as db:
        watch = db.get(Watch, watch_id)
        assert watch is not None
        watch.verification_email_sent_at = (
            datetime.now(timezone.utc) - timedelta(minutes=2)
        )
        db.commit()

    def fake_send_watch_verification_email(**kwargs: object) -> str:
        sent_watch_ids.append(int(kwargs["watch_id"]))
        return "https://example.com/verify"

    monkeypatch.setattr(
        watch_routes,
        "send_watch_verification_email",
        fake_send_watch_verification_email,
    )

    client = TestClient(app)

    response = client.post(
        f"/watches/{watch_id}/resend-verification",
        data={
            "email": "route@example.com",
        },
    )

    with route_db() as db:
        watch = db.get(Watch, watch_id)

    assert response.status_code == 200
    assert sent_watch_ids == [watch_id]
    assert watch is not None
    assert watch.verification_resend_count == 1
    assert "Resend 1 of 3 used" in response.text


def test_pending_watch_resend_has_one_minute_cooldown(
    route_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    watch_id = add_watch(route_db, active=False, confirmed=False)
    sent_watch_ids: list[int] = []

    with route_db() as db:
        watch = db.get(Watch, watch_id)
        assert watch is not None
        watch.verification_resend_count = 1
        watch.verification_email_sent_at = datetime.now(timezone.utc)
        db.commit()

    def fake_send_watch_verification_email(**kwargs: object) -> str:
        sent_watch_ids.append(int(kwargs["watch_id"]))
        return "https://example.com/verify"

    monkeypatch.setattr(
        watch_routes,
        "send_watch_verification_email",
        fake_send_watch_verification_email,
    )

    client = TestClient(app)

    response = client.post(
        f"/watches/{watch_id}/resend-verification",
        data={
            "email": "route@example.com",
        },
    )

    assert response.status_code == 429
    assert sent_watch_ids == []
    assert "Wait about" in response.text


def test_first_pending_watch_resend_is_not_blocked_by_original_email(
    route_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    watch_id = add_watch(route_db, active=False, confirmed=False)
    sent_watch_ids: list[int] = []

    with route_db() as db:
        watch = db.get(Watch, watch_id)
        assert watch is not None
        watch.verification_resend_count = 0
        watch.verification_email_sent_at = datetime.now(timezone.utc)
        db.commit()

    def fake_send_watch_verification_email(**kwargs: object) -> str:
        sent_watch_ids.append(int(kwargs["watch_id"]))
        return "https://example.com/verify"

    monkeypatch.setattr(
        watch_routes,
        "send_watch_verification_email",
        fake_send_watch_verification_email,
    )

    client = TestClient(app)

    response = client.post(
        f"/watches/{watch_id}/resend-verification",
        data={
            "email": "route@example.com",
        },
    )

    with route_db() as db:
        watch = db.get(Watch, watch_id)

    assert response.status_code == 200
    assert sent_watch_ids == [watch_id]
    assert watch is not None
    assert watch.verification_resend_count == 1
    assert "Resend 1 of 3 used" in response.text


def test_pending_watch_resend_stops_after_three_tries(
    route_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    watch_id = add_watch(route_db, active=False, confirmed=False)
    sent_watch_ids: list[int] = []

    with route_db() as db:
        watch = db.get(Watch, watch_id)
        assert watch is not None
        watch.verification_resend_count = 3
        watch.verification_email_sent_at = (
            datetime.now(timezone.utc) - timedelta(minutes=2)
        )
        db.commit()

    def fake_send_watch_verification_email(**kwargs: object) -> str:
        sent_watch_ids.append(int(kwargs["watch_id"]))
        return "https://example.com/verify"

    monkeypatch.setattr(
        watch_routes,
        "send_watch_verification_email",
        fake_send_watch_verification_email,
    )

    client = TestClient(app)

    response = client.post(
        f"/watches/{watch_id}/resend-verification",
        data={
            "email": "route@example.com",
        },
    )

    assert response.status_code == 429
    assert sent_watch_ids == []
    assert "used all 3 verification resends" in response.text


def test_manage_page_loads(route_db) -> None:
    client = TestClient(app)

    response = client.get("/manage")

    assert response.status_code == 200
    assert "Manage your watches" in response.text


def test_manage_request_sends_generic_confirmation_for_known_email(
    route_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_watch(route_db, email="known@example.com")
    sent_to: list[str] = []

    def fake_send_manage_watches_email(**kwargs: object) -> str:
        sent_to.append(str(kwargs["email"]))
        return "https://example.com/manage/token"

    monkeypatch.setattr(
        watch_routes,
        "send_manage_watches_email",
        fake_send_manage_watches_email,
    )

    client = TestClient(app)

    response = client.post(
        "/manage",
        data={"email": " known@example.com "},
    )

    assert response.status_code == 200
    assert "If that email has watches with UW Seat Watch" in response.text
    assert "known@example.com" not in response.text
    assert sent_to == ["known@example.com"]


def test_manage_request_sends_same_confirmation_for_unknown_email(
    route_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent_to: list[str] = []

    def fake_send_manage_watches_email(**kwargs: object) -> str:
        sent_to.append(str(kwargs["email"]))
        return "https://example.com/manage/token"

    monkeypatch.setattr(
        watch_routes,
        "send_manage_watches_email",
        fake_send_manage_watches_email,
    )

    client = TestClient(app)

    response = client.post(
        "/manage",
        data={"email": "unknown@example.com"},
    )

    assert response.status_code == 200
    assert "If that email has watches with UW Seat Watch" in response.text
    assert "No watches found" not in response.text
    assert "unknown@example.com" not in response.text
    assert sent_to == []


def test_manage_link_can_be_resent_after_cooldown(
    route_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_watch(route_db, email="known@example.com")
    sent_to: list[str] = []

    def fake_send_manage_watches_email(**kwargs: object) -> str:
        sent_to.append(str(kwargs["email"]))
        return "https://example.com/manage/token"

    monkeypatch.setattr(
        watch_routes,
        "send_manage_watches_email",
        fake_send_manage_watches_email,
    )

    client = TestClient(app)
    initial_response = client.post(
        "/manage",
        data={"email": "known@example.com"},
    )
    resend_token = manage_resend_token(initial_response)
    watch_routes.manage_link_requests["known@example.com"][
        "last_sent_at"
    ] = datetime.now(timezone.utc) - timedelta(minutes=2)

    response = client.post(
        "/manage/resend",
        data={"resend_token": resend_token},
    )

    assert response.status_code == 200
    assert sent_to == ["known@example.com", "known@example.com"]
    assert "another link was sent" in response.text


def test_manage_link_resend_has_one_minute_cooldown(
    route_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_watch(route_db, email="known@example.com")
    sent_to: list[str] = []

    def fake_send_manage_watches_email(**kwargs: object) -> str:
        sent_to.append(str(kwargs["email"]))
        return "https://example.com/manage/token"

    monkeypatch.setattr(
        watch_routes,
        "send_manage_watches_email",
        fake_send_manage_watches_email,
    )

    client = TestClient(app)
    initial_response = client.post(
        "/manage",
        data={"email": "known@example.com"},
    )
    resend_token = manage_resend_token(initial_response)

    response = client.post(
        "/manage/resend",
        data={"resend_token": resend_token},
    )

    assert response.status_code == 429
    assert sent_to == ["known@example.com"]
    assert "Wait about" in response.text


def test_manage_link_resend_stops_after_two_tries(
    route_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_watch(route_db, email="known@example.com")
    sent_to: list[str] = []

    def fake_send_manage_watches_email(**kwargs: object) -> str:
        sent_to.append(str(kwargs["email"]))
        return "https://example.com/manage/token"

    monkeypatch.setattr(
        watch_routes,
        "send_manage_watches_email",
        fake_send_manage_watches_email,
    )

    client = TestClient(app)
    initial_response = client.post(
        "/manage",
        data={"email": "known@example.com"},
    )
    resend_token = manage_resend_token(initial_response)

    for _ in range(2):
        watch_routes.manage_link_requests["known@example.com"][
            "last_sent_at"
        ] = datetime.now(timezone.utc) - timedelta(minutes=2)
        client.post(
            "/manage/resend",
            data={"resend_token": resend_token},
        )

    watch_routes.manage_link_requests["known@example.com"][
        "last_sent_at"
    ] = datetime.now(timezone.utc) - timedelta(minutes=2)
    response = client.post(
        "/manage/resend",
        data={"resend_token": resend_token},
    )

    assert response.status_code == 429
    assert sent_to == [
        "known@example.com",
        "known@example.com",
        "known@example.com",
    ]
    assert "used both secure-link resends" in response.text


def test_manage_token_views_only_authorized_subscriber_watches(
    route_db,
) -> None:
    own_watch_id = add_watch(
        route_db,
        email="owner@example.com",
        section_name="LEC 001",
    )
    other_watch_id = add_watch(
        route_db,
        email="other@example.com",
        class_number="3900",
        section_name="TUT 101",
    )

    with route_db() as db:
        own_watch = db.get(Watch, own_watch_id)
        other_watch = db.get(Watch, other_watch_id)
        assert own_watch is not None
        assert other_watch is not None
        token = create_manage_watches_token(
            subscriber_id=own_watch.subscriber_id,
            email="owner@example.com",
        )

    client = TestClient(app)

    response = client.get(f"/manage/{token}")

    assert response.status_code == 200
    assert "o***@example.com" in response.text
    assert "LEC 001" in response.text
    assert "TUT 101" not in response.text
    assert "owner@example.com" not in response.text
    assert "other@example.com" not in response.text


def test_manage_token_rejects_tampering(route_db) -> None:
    watch_id = add_watch(route_db)

    with route_db() as db:
        watch = db.get(Watch, watch_id)
        assert watch is not None
        token = create_manage_watches_token(
            subscriber_id=watch.subscriber_id,
            email="route@example.com",
        )

    client = TestClient(app)

    response = client.get(f"/manage/{token}x")

    assert response.status_code == 400
    assert "This link has expired" in response.text


def test_manage_token_rejects_expired_link(
    route_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    watch_id = add_watch(route_db)

    with route_db() as db:
        watch = db.get(Watch, watch_id)
        assert watch is not None
        token = create_manage_watches_token(
            subscriber_id=watch.subscriber_id,
            email="route@example.com",
        )

    monkeypatch.setattr(
        "app.services.tokens.MANAGE_TOKEN_MAX_AGE_SECONDS",
        -1,
    )
    client = TestClient(app)

    response = client.get(f"/manage/{token}")

    assert response.status_code == 400
    assert "This link has expired" in response.text


def test_manage_cannot_remove_another_subscribers_watch(route_db) -> None:
    own_watch_id = add_watch(
        route_db,
        email="owner@example.com",
        active=True,
    )
    other_watch_id = add_watch(
        route_db,
        email="other@example.com",
        active=True,
        class_number="3900",
        section_name="TUT 101",
    )

    with route_db() as db:
        own_watch = db.get(Watch, own_watch_id)
        assert own_watch is not None
        token = create_manage_watches_token(
            subscriber_id=own_watch.subscriber_id,
            email="owner@example.com",
        )

    client = TestClient(app)

    response = client.post(
        f"/manage/{token}/watches/{other_watch_id}/remove"
    )

    with route_db() as db:
        own_watch = db.get(Watch, own_watch_id)
        other_watch = db.get(Watch, other_watch_id)

    assert response.status_code == 200
    assert own_watch is not None
    assert other_watch is not None
    assert own_watch.active is True
    assert other_watch.active is True


def test_manage_individual_removal_deletes_watch(route_db) -> None:
    watch_id = add_watch(route_db, active=True)

    with route_db() as db:
        watch = db.get(Watch, watch_id)
        assert watch is not None
        token = create_manage_watches_token(
            subscriber_id=watch.subscriber_id,
            email="route@example.com",
        )

    client = TestClient(app)

    response = client.post(f"/manage/{token}/watches/{watch_id}/remove")

    with route_db() as db:
        watch = db.get(Watch, watch_id)

    assert response.status_code == 200
    assert watch is None


def test_manage_stop_all_only_deletes_authorized_subscriber_watches(
    route_db,
) -> None:
    own_watch_id = add_watch(
        route_db,
        email="owner@example.com",
        active=True,
    )
    other_watch_id = add_watch(
        route_db,
        email="other@example.com",
        active=True,
        class_number="3900",
        section_name="TUT 101",
    )

    with route_db() as db:
        own_watch = db.get(Watch, own_watch_id)
        assert own_watch is not None
        token = create_manage_watches_token(
            subscriber_id=own_watch.subscriber_id,
            email="owner@example.com",
        )

    client = TestClient(app)

    response = client.post(f"/manage/{token}/watches/stop-all")

    with route_db() as db:
        own_watch = db.get(Watch, own_watch_id)
        other_watch = db.get(Watch, other_watch_id)

    assert response.status_code == 200
    assert own_watch is None
    assert other_watch is not None
    assert other_watch.active is True


def test_manage_get_requests_cannot_delete(route_db) -> None:
    watch_id = add_watch(route_db, active=True)

    with route_db() as db:
        watch = db.get(Watch, watch_id)
        assert watch is not None
        token = create_manage_watches_token(
            subscriber_id=watch.subscriber_id,
            email="route@example.com",
        )

    client = TestClient(app)

    response = client.get(f"/manage/{token}/watches/{watch_id}/remove")

    with route_db() as db:
        watch = db.get(Watch, watch_id)

    assert response.status_code == 405
    assert watch is not None
    assert watch.active is True


def test_new_watch_can_be_created_after_stop_all(
    route_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    watch_id = add_watch(route_db, active=True)

    with route_db() as db:
        watch = db.get(Watch, watch_id)
        assert watch is not None
        token = create_manage_watches_token(
            subscriber_id=watch.subscriber_id,
            email="route@example.com",
        )

    client = TestClient(app)
    client.post(f"/manage/{token}/watches/stop-all")

    async def fake_fetch_course_html(**kwargs: str) -> str:
        return "<html>ok</html>"

    def fake_parse_course_sections(html: str) -> list[CourseSection]:
        return [
            CourseSection(
                class_number="3900",
                section_number="TUT 101",
                campus_type="UW U",
                associated_class="1",
                enrollment_capacity=70,
                enrollment_total=70,
                waitlist_capacity=0,
                waitlist_total=0,
                meeting_time="",
                room="",
            )
        ]

    monkeypatch.setattr(
        watch_routes,
        "fetch_course_html",
        fake_fetch_course_html,
    )
    monkeypatch.setattr(
        watch_routes,
        "parse_course_sections",
        fake_parse_course_sections,
    )
    monkeypatch.setattr(
        watch_routes,
        "send_watch_verification_email",
        lambda **kwargs: "https://example.com/verify",
    )

    response = client.post(
        "/watches",
        data={
            "level": "under",
            "term": "1269",
            "subject": "AFM",
            "catalog_number": "101",
            "class_number": "3900",
            "email": "route@example.com",
        },
    )

    with route_db() as db:
        watches = db.scalars(
            select(Watch).join(Subscriber).where(
                Subscriber.email == "route@example.com"
            )
        ).all()

    assert response.status_code == 201
    assert len(watches) == 1


def test_same_watch_can_be_created_after_manage_remove(
    route_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    watch_id = add_watch(route_db, active=True)

    with route_db() as db:
        watch = db.get(Watch, watch_id)
        assert watch is not None
        token = create_manage_watches_token(
            subscriber_id=watch.subscriber_id,
            email="route@example.com",
        )

    client = TestClient(app)
    client.post(f"/manage/{token}/watches/{watch_id}/remove")

    async def fake_fetch_course_html(**kwargs: str) -> str:
        return "<html>ok</html>"

    def fake_parse_course_sections(html: str) -> list[CourseSection]:
        return [
            CourseSection(
                class_number="3804",
                section_number="LEC 001",
                campus_type="UW U",
                associated_class="1",
                enrollment_capacity=70,
                enrollment_total=70,
                waitlist_capacity=0,
                waitlist_total=0,
                meeting_time="",
                room="",
            )
        ]

    monkeypatch.setattr(
        watch_routes,
        "fetch_course_html",
        fake_fetch_course_html,
    )
    monkeypatch.setattr(
        watch_routes,
        "parse_course_sections",
        fake_parse_course_sections,
    )
    monkeypatch.setattr(
        watch_routes,
        "send_watch_verification_email",
        lambda **kwargs: "https://example.com/verify",
    )

    response = client.post(
        "/watches",
        data={
            "level": "under",
            "term": "1269",
            "subject": "AFM",
            "catalog_number": "101",
            "class_number": "3804",
            "email": "route@example.com",
        },
    )

    with route_db() as db:
        watches = db.scalars(
            select(Watch).join(Subscriber).where(
                Subscriber.email == "route@example.com"
            )
        ).all()

    assert response.status_code == 201
    assert len(watches) == 1
    assert watches[0].class_number == "3804"


def test_unsubscribe_route_deletes_watch(route_db) -> None:
    watch_id = add_watch(route_db, active=True, confirmed=True)
    token = create_watch_unsubscribe_token(watch_id)
    client = TestClient(app)

    response = client.get(f"/unsubscribe?token={token}")

    with route_db() as db:
        watch = db.get(Watch, watch_id)

    assert response.status_code == 200
    assert watch is None


def test_unsubscribe_route_rejects_invalid_token(route_db) -> None:
    client = TestClient(app)

    response = client.get("/unsubscribe?token=not-a-real-token")

    assert response.status_code == 400


def test_repeated_unsubscribe_is_safe(route_db) -> None:
    watch_id = add_watch(route_db, active=True, confirmed=True)
    token = create_watch_unsubscribe_token(watch_id)
    client = TestClient(app)

    first_response = client.get(f"/unsubscribe?token={token}")
    second_response = client.get(f"/unsubscribe?token={token}")

    with route_db() as db:
        watch = db.get(Watch, watch_id)

    assert first_response.status_code == 200
    assert second_response.status_code == 404
    assert watch is None
