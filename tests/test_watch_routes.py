from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models import Notification, SectionState, Subscriber, Watch
from app.services.tokens import (
    create_watch_unsubscribe_token,
    create_watch_verification_token,
)


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

    def override_get_db():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    yield TestSessionLocal

    app.dependency_overrides.clear()
    engine.dispose()


def add_watch(
    route_db,
    *,
    active: bool = False,
    confirmed: bool = False,
    last_seen_open: bool = False,
) -> int:
    with route_db() as db:
        subscriber = Subscriber(email="route@example.com")
        subscriber.watches.append(
            Watch(
                level="under",
                term="1269",
                subject="AFM",
                catalog_number="101",
                class_number="3804",
                section_name="LEC 001",
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


def test_unsubscribe_route_deactivates_watch(route_db) -> None:
    watch_id = add_watch(route_db, active=True, confirmed=True)
    token = create_watch_unsubscribe_token(watch_id)
    client = TestClient(app)

    response = client.get(f"/unsubscribe?token={token}")

    with route_db() as db:
        watch = db.get(Watch, watch_id)

    assert response.status_code == 200
    assert watch is not None
    assert watch.active is False


def test_unsubscribe_route_rejects_invalid_token(route_db) -> None:
    client = TestClient(app)

    response = client.get("/unsubscribe?token=not-a-real-token")

    assert response.status_code == 400


def test_repeated_unsubscribe_is_safe(route_db) -> None:
    watch_id = add_watch(route_db, active=False, confirmed=True)
    token = create_watch_unsubscribe_token(watch_id)
    client = TestClient(app)

    response = client.get(f"/unsubscribe?token={token}")

    with route_db() as db:
        watch = db.get(Watch, watch_id)

    assert response.status_code == 200
    assert watch is not None
    assert watch.active is False
