from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Notification, Subscriber, Watch
from app.services import outbox
from app.services.email import EmailDeliveryError


def create_test_database(tmp_path: Path):
    database_path = (tmp_path / "outbox.db").resolve().as_posix()
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
def outbox_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    engine, TestSessionLocal = create_test_database(tmp_path)
    monkeypatch.setattr(outbox, "SessionLocal", TestSessionLocal)

    yield TestSessionLocal

    engine.dispose()


def add_watch_and_notification(outbox_db, *, active: bool = True) -> tuple[int, int]:
    payload = {
        "email": "notify@example.com",
        "subject": "AFM",
        "catalog_number": "101",
        "section_name": "LEC 001",
        "current_enrollment": 69,
        "current_capacity": 70,
    }

    with outbox_db() as db:
        subscriber = Subscriber(email="notify@example.com")
        subscriber.watches.append(
            Watch(
                level="under",
                term="1269",
                subject="AFM",
                catalog_number="101",
                class_number="3804",
                section_name="LEC 001",
                active=active,
            )
        )
        db.add(subscriber)
        db.flush()

        notification = Notification(
            watch_id=subscriber.watches[0].id,
            kind="section_open",
            payload=json.dumps(payload),
        )
        db.add(notification)
        db.commit()

        return notification.id, subscriber.watches[0].id


def test_outbox_success_marks_notification_sent(
    outbox_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notification_id, watch_id = add_watch_and_notification(outbox_db)
    sent: list[int] = []

    def fake_send_opening_alert(**kwargs: object) -> None:
        sent.append(int(kwargs["watch_id"]))

    monkeypatch.setattr(
        outbox,
        "send_opening_alert",
        fake_send_opening_alert,
    )

    sent_count = outbox.send_pending_notifications()

    with outbox_db() as db:
        notification = db.get(Notification, notification_id)
        watch = db.get(Watch, watch_id)

    assert sent_count == 1
    assert len(sent) == 1
    assert notification is not None
    assert notification.sent_at is not None
    assert watch is not None
    assert watch.last_notified_at == notification.sent_at
    assert notification.attempt_count == 1
    assert notification.last_error is None


def test_outbox_failure_is_retried_then_marked_sent(
    outbox_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notification_id, _watch_id = add_watch_and_notification(outbox_db)
    attempts = 0

    def flaky_send_opening_alert(**kwargs: object) -> None:
        nonlocal attempts
        attempts += 1

        if attempts == 1:
            raise EmailDeliveryError("temporary email failure")

    monkeypatch.setattr(
        outbox,
        "send_opening_alert",
        flaky_send_opening_alert,
    )

    first_sent_count = outbox.send_pending_notifications()

    with outbox_db() as db:
        first_notification = db.get(Notification, notification_id)

    second_sent_count = outbox.send_pending_notifications()

    with outbox_db() as db:
        second_notification = db.get(Notification, notification_id)

    assert first_sent_count == 0
    assert first_notification is not None
    assert first_notification.sent_at is None
    assert first_notification.attempt_count == 1
    assert "temporary email failure" in first_notification.last_error
    assert second_sent_count == 1
    assert attempts == 2
    assert second_notification is not None
    assert second_notification.sent_at is not None
    assert second_notification.attempt_count == 2
    assert second_notification.last_error is None


def test_inactive_watch_notification_is_cancelled_without_email(
    outbox_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notification_id, _watch_id = add_watch_and_notification(
        outbox_db,
        active=False,
    )
    sent: list[int] = []

    def fake_send_opening_alert(**kwargs: object) -> None:
        sent.append(int(kwargs["watch_id"]))

    monkeypatch.setattr(
        outbox,
        "send_opening_alert",
        fake_send_opening_alert,
    )

    sent_count = outbox.send_pending_notifications()

    with outbox_db() as db:
        notification = db.get(Notification, notification_id)

    assert sent_count == 0
    assert sent == []
    assert notification is not None
    assert notification.sent_at is not None
    assert "cancelled" in notification.last_error
