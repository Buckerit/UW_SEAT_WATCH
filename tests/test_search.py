from __future__ import annotations

from datetime import date

from app.routes.search import get_default_term_code


def test_default_term_code_uses_next_enrollment_term() -> None:
    assert get_default_term_code(date(2026, 1, 15)) == "1265"
    assert get_default_term_code(date(2026, 8, 20)) == "1269"
    assert get_default_term_code(date(2026, 9, 15)) == "1271"
