from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Watch


TERM_END_MONTH_DAY = {
    "1": (4, 30),
    "5": (8, 31),
    "9": (12, 31),
}


def get_term_end_date(term: str) -> date | None:
    """Convert a Waterloo term code like 1269 into its term end date."""
    if len(term) != 4 or not term.isdigit():
        return None

    month_day = TERM_END_MONTH_DAY.get(term[-1])
    if month_day is None:
        return None

    year = 2000 + int(term[1:3])
    month, day = month_day

    return date(year, month, day)


def cleanup_ended_term_watches(
    now: datetime | None = None,
) -> int:
    """Delete watches for terms whose end date has passed."""
    current_date = (now or datetime.now(timezone.utc)).date()
    deleted_count = 0

    with SessionLocal() as db:
        watches = db.scalars(select(Watch)).all()

        for watch in watches:
            term_end_date = get_term_end_date(watch.term)

            if term_end_date is None:
                continue

            if current_date > term_end_date:
                db.delete(watch)
                deleted_count += 1

        db.commit()

    return deleted_count
