from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routes import search
from app.waterloo.parser import CourseSection


@pytest.mark.parametrize(
    ("friendly_term", "waterloo_term"),
    [
        ("W25", "1251"),
        ("S25", "1255"),
        ("F25", "1259"),
        ("W26", "1261"),
        ("F26", "1269"),
        ("W27", "1271"),
    ],
)
def test_friendly_term_to_waterloo_term(
    friendly_term: str,
    waterloo_term: str,
) -> None:
    assert search.friendly_term_to_waterloo_term(friendly_term) == waterloo_term


def test_friendly_term_normalizes_lowercase_and_whitespace() -> None:
    assert search.normalize_friendly_term_code(" f26 ") == "F26"
    assert search.friendly_term_to_waterloo_term(" f26 ") == "1269"
    assert search.friendly_term_name(" f26 ") == "Fall 2026"


@pytest.mark.parametrize(
    "term",
    ["2026", "Fall26", "F2", "F2026", "X26", ""],
)
def test_malformed_term_inputs_show_friendly_error(term: str) -> None:
    client = TestClient(app)

    response = client.post(
        "/search",
        data={
            "level": "under",
            "term": term,
            "subject": "AFM",
            "catalog_number": "101",
        },
    )

    assert response.status_code == 422
    assert "Enter a term in the format W27, S27, or F27." in response.text


def test_syntactically_valid_unpublished_term_is_friendly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch_published_term_codes() -> set[str]:
        return {"1269"}

    monkeypatch.setattr(
        search,
        "fetch_published_term_codes",
        fake_fetch_published_term_codes,
    )

    client = TestClient(app)

    response = client.post(
        "/search",
        data={
            "level": "under",
            "term": "F27",
            "subject": "AFM",
            "catalog_number": "101",
        },
    )

    assert response.status_code == 422
    assert (
        "Fall 2027 is not currently available in Waterloo&#39;s "
        "Schedule of Classes."
    ) in response.text


def test_published_term_nonexistent_course_is_friendly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch_published_term_codes() -> set[str]:
        return {"1269"}

    async def fake_fetch_course_html(**kwargs: str) -> str:
        return "<html>ok</html>"

    monkeypatch.setattr(
        search,
        "fetch_published_term_codes",
        fake_fetch_published_term_codes,
    )
    monkeypatch.setattr(search, "fetch_course_html", fake_fetch_course_html)
    monkeypatch.setattr(search, "parse_course_sections", lambda html: [])

    client = TestClient(app)

    response = client.post(
        "/search",
        data={
            "level": "under",
            "term": "F26",
            "subject": "COMMST",
            "catalog_number": "999",
        },
    )

    assert response.status_code == 404
    assert "No sections were found for COMMST 999 in Fall 2026." in response.text


def test_valid_search_passes_canonical_term_to_waterloo_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    async def fake_fetch_published_term_codes() -> set[str]:
        return {"1269"}

    async def fake_fetch_course_html(**kwargs: str) -> str:
        captured.update(kwargs)
        return "<html>ok</html>"

    monkeypatch.setattr(
        search,
        "fetch_published_term_codes",
        fake_fetch_published_term_codes,
    )
    monkeypatch.setattr(search, "fetch_course_html", fake_fetch_course_html)
    monkeypatch.setattr(
        search,
        "parse_course_sections",
        lambda html: [
            CourseSection(
                class_number="3804",
                section_number="LEC 001",
                campus_type="UW U",
                associated_class="1",
                enrollment_capacity=70,
                enrollment_total=69,
                waitlist_capacity=0,
                waitlist_total=0,
                meeting_time="",
                room="",
            )
        ],
    )

    client = TestClient(app)

    response = client.post(
        "/search",
        data={
            "level": "under",
            "term": " f26 ",
            "subject": "AFM",
            "catalog_number": "101",
        },
    )

    assert response.status_code == 200
    assert captured["term"] == "1269"
    assert "Fall 2026" in response.text
    assert 'name="term"' in response.text
    assert 'value="1269"' in response.text


def test_default_term_code_uses_next_enrollment_term() -> None:
    assert search.get_default_term_code(date(2026, 1, 15)) == "S26"
    assert search.get_default_term_code(date(2026, 8, 20)) == "F26"
    assert search.get_default_term_code(date(2026, 9, 15)) == "W27"
