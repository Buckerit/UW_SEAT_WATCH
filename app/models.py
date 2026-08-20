from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


class Subscriber(Base):
    __tablename__ = "subscribers"

    id: Mapped[int] = mapped_column(
        primary_key=True,
    )

    email: Mapped[str] = mapped_column(
        String(320),
        unique=True,
        index=True,
        nullable=False,
    )

    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    watches: Mapped[list[Watch]] = relationship(
        back_populates="subscriber",
        cascade="all, delete-orphan",
    )

 
class Watch(Base):
    __tablename__ = "watches"

    __table_args__ = (
        UniqueConstraint(
            "subscriber_id",
            "term",
            "class_number",
            name="uq_watch_subscriber_term_class",
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
    )
    

    subscriber_id: Mapped[int] = mapped_column(
        ForeignKey(
            "subscribers.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    level: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
    )

    term: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
    )

    subject: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )

    catalog_number: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    class_number: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    section_name: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    active: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_notified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_seen_open: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    subscriber: Mapped[Subscriber] = relationship(
        back_populates="watches"
    )
    
class SectionState(Base):
    __tablename__ = "section_states"

    __table_args__ = (
        UniqueConstraint(
            "term",
            "class_number",
            name="uq_section_state_term_class",
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
    )

    level: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
    )

    term: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
    )

    subject: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )

    catalog_number: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    class_number: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    section_name: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    enrollment_capacity: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    enrollment_total: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    waitlist_capacity: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    waitlist_total: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    last_checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    @property
    def available_seats(self) -> int:
        return max(
            self.enrollment_capacity - self.enrollment_total,
            0,
        )

    @property
    def appears_open(self) -> bool:
        return self.enrollment_total < self.enrollment_capacity


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(
        primary_key=True,
    )

    watch_id: Mapped[int] = mapped_column(
        ForeignKey(
            "watches.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    kind: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    payload: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    attempt_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    last_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
