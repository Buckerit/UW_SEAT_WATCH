from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, event, func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Subscriber, Watch


def create_test_database(tmp_path: Path):
    database_path = (tmp_path / "test.db").resolve().as_posix()

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

def make_watch(**overrides: str) -> Watch:
    values = {
        "level": "under",
        "term": "1269",
        "subject": "AFM",
        "catalog_number": "101",
        "class_number": "3804",
        "section_name": "LEC 001",
    }

    values.update(overrides)

    return Watch(**values)


def test_subscriber_can_be_saved_and_retrieved(tmp_path: Path) -> None:
    engine, TestSessionLocal = create_test_database(tmp_path)

    assert "subscribers" in inspect(engine).get_table_names()

    with TestSessionLocal() as db:
        subscriber = Subscriber(
            email="database-test@example.com",
        )

        db.add(subscriber)
        db.commit()

        subscriber_id = subscriber.id

    with TestSessionLocal() as db:
        saved_subscriber = db.scalar(
            select(Subscriber).where(
                Subscriber.id == subscriber_id
            )
        )

        assert saved_subscriber is not None
        assert saved_subscriber.email == "database-test@example.com"
        assert saved_subscriber.verified_at is None
        assert saved_subscriber.created_at is not None
        assert saved_subscriber.updated_at is not None

    engine.dispose()


def test_duplicate_subscriber_email_is_rejected(tmp_path: Path) -> None:
    engine, TestSessionLocal = create_test_database(tmp_path)

    with TestSessionLocal() as db:
        first_subscriber = Subscriber(
            email="duplicate@example.com",
        )

        db.add(first_subscriber)
        db.commit()

        duplicate_subscriber = Subscriber(
            email="duplicate@example.com",
        )

        db.add(duplicate_subscriber)

        with pytest.raises(IntegrityError):
            db.commit()

        db.rollback()

    engine.dispose()
    
def test_watch_can_be_saved_for_subscriber(tmp_path: Path) -> None:
    engine, TestSessionLocal = create_test_database(tmp_path)

    with TestSessionLocal() as db:
        subscriber = Subscriber(
            email="watch-owner@example.com",
        )

        watch = make_watch()

        subscriber.watches.append(watch)

        db.add(subscriber)
        db.commit()

        subscriber_id = subscriber.id
        watch_id = watch.id

        assert watch.subscriber_id == subscriber_id
        assert watch.subscriber is subscriber
        assert watch.active is False
        assert watch.confirmed_at is None

    with TestSessionLocal() as db:
        saved_watch = db.get(Watch, watch_id)
        saved_subscriber = db.get(Subscriber, subscriber_id)

        assert saved_watch is not None
        assert saved_subscriber is not None

        assert saved_watch.subscriber_id == saved_subscriber.id
        assert saved_watch.subscriber.email == "watch-owner@example.com"

        assert len(saved_subscriber.watches) == 1
        assert saved_subscriber.watches[0].id == saved_watch.id
        assert saved_subscriber.watches[0].section_name == "LEC 001"

    engine.dispose()
    
def test_duplicate_watch_is_rejected_for_same_subscriber(
    tmp_path: Path,
) -> None:
    engine, TestSessionLocal = create_test_database(tmp_path)

    with TestSessionLocal() as db:
        subscriber = Subscriber(
            email="duplicate-watch@example.com",
        )

        subscriber.watches.append(make_watch())

        db.add(subscriber)
        db.commit()

        duplicate_watch = make_watch(
            section_name="LEC 999",
        )

        subscriber.watches.append(duplicate_watch)

        with pytest.raises(IntegrityError):
            db.commit()

        db.rollback()

    engine.dispose()
    
def test_same_class_can_be_watched_by_different_subscribers(
    tmp_path: Path,
) -> None:
    engine, TestSessionLocal = create_test_database(tmp_path)

    with TestSessionLocal() as db:
        first_subscriber = Subscriber(
            email="first-watcher@example.com",
        )
        second_subscriber = Subscriber(
            email="second-watcher@example.com",
        )

        first_subscriber.watches.append(make_watch())
        second_subscriber.watches.append(make_watch())

        db.add_all(
            [
                first_subscriber,
                second_subscriber,
            ]
        )
        db.commit()

        watch_count = db.scalar(
            select(func.count()).select_from(Watch)
        )

        assert watch_count == 2

    engine.dispose()

def test_subscriber_can_watch_same_class_number_in_different_terms(
    tmp_path: Path,
) -> None:
    engine, TestSessionLocal = create_test_database(tmp_path)

    with TestSessionLocal() as db:
        subscriber = Subscriber(
            email="multi-term@example.com",
        )

        fall_watch = make_watch(
            term="1269",
        )
        winter_watch = make_watch(
            term="1271",
        )

        subscriber.watches.extend(
            [
                fall_watch,
                winter_watch,
            ]
        )

        db.add(subscriber)
        db.commit()

        watch_count = db.scalar(
            select(func.count()).select_from(Watch)
        )

        assert watch_count == 2

    engine.dispose()
    
def test_deleting_subscriber_deletes_their_watches(
    tmp_path: Path,
) -> None:
    engine, TestSessionLocal = create_test_database(tmp_path)

    with TestSessionLocal() as db:
        subscriber = Subscriber(
            email="delete-me@example.com",
        )

        subscriber.watches.extend(
            [
                make_watch(),
                make_watch(
                    class_number="3840",
                    section_name="TUT 101",
                ),
            ]
        )

        db.add(subscriber)
        db.commit()

        subscriber_id = subscriber.id
        watch_ids = [
            watch.id
            for watch in subscriber.watches
        ]

        db.delete(subscriber)
        db.commit()

    with TestSessionLocal() as db:
        assert db.get(Subscriber, subscriber_id) is None

        for watch_id in watch_ids:
            assert db.get(Watch, watch_id) is None

    engine.dispose()
    
    
def test_watch_cannot_reference_missing_subscriber(
    tmp_path: Path,
) -> None:
    engine, TestSessionLocal = create_test_database(tmp_path)

    with TestSessionLocal() as db:
        watch = make_watch()
        watch.subscriber_id = 999_999

        db.add(watch)

        with pytest.raises(IntegrityError):
            db.commit()

        db.rollback()

    engine.dispose()
    
    

    
    