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

    assert lecture.section_number == "LEC 001"
    assert lecture.enrollment_capacity == 70
    assert lecture.enrollment_total == 49
    assert lecture.available_seats == 21
    assert lecture.appears_open is True


def test_ignores_nested_repeat_and_reserve_rows() -> None:
    html = """
    <table>
      <tr>
        <td>3804</td><td>LEC 001</td><td>UW U</td><td>1</td><td></td><td>201</td>
        <td>70</td><td>70</td><td>0</td><td>0</td><td>02:30-03:50W</td><td></td>
      </tr>
      <tr>
        <td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td>
        <td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td><td>02:30-03:50M</td><td></td>
      </tr>
      <tr>
        <td colspan="6"><i>Reserve: Year 1 Math Students</i></td>
        <td>100</td><td>93</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td>
      </tr>
      <tr>
        <td>
          <table>
            <tr>
              <td>Reserve: Year 1 Math Students</td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
    """

    sections = parse_course_sections(html)

    assert [section.class_number for section in sections] == ["3804"]
