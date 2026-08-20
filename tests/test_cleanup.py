from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Subscriber, Watch
from app.services import cleanup


def create_test_database(tmp_path: Path):
    database_path = (tmp_path / "cleanup.db").resolve().as_posix()
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
def cleanup_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    engine, TestSessionLocal = create_test_database(tmp_path)
    monkeypatch.setattr(cleanup, "SessionLocal", TestSessionLocal)

    yield TestSessionLocal

    engine.dispose()


def add_watch(cleanup_db, *, term: str) -> None:
    with cleanup_db() as db:
        subscriber = Subscriber(email=f"{term}@example.com")
        subscriber.watches.append(
            Watch(
                level="under",
                term=term,
                subject="AFM",
                catalog_number="101",
                class_number=term,
                section_name="LEC 001",
                active=True,
            )
        )
        db.add(subscriber)
        db.commit()


def test_cleanup_deletes_only_ended_term_watches(cleanup_db) -> None:
    add_watch(cleanup_db, term="1265")
    add_watch(cleanup_db, term="1269")

    deleted_count = cleanup.cleanup_ended_term_watches(
        now=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )

    with cleanup_db() as db:
        remaining_terms = db.scalars(select(Watch.term)).all()
        watch_count = db.scalar(select(func.count()).select_from(Watch))

    assert deleted_count == 1
    assert watch_count == 1
    assert remaining_terms == ["1269"]
