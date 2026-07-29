from pathlib import Path
from app.waterloo.parser import parse_course_sections

FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "afm101.html"
)

def test_parses_afm_101_sections() -> None:
    html = FIXTURE.read_text(encoding="utf-8")
    sections = parse_course_sections(html)

    assert len(sections) == 6

    lecture = next(
        section
        for section in sections
        if section.class_number == "3804"
    )

    assert lecture.section_name == "LEC 001"
    assert lecture.enrollment_capacity == 70
    assert lecture.enrollment_total == 49
    assert lecture.available_seats == 21
    assert lecture.appears_open is True