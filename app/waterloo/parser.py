from __future__ import annotations
import re
from dataclasses import dataclass
from bs4 import BeautifulSoup
from bs4.element import Tag

SECTION_PATTERN = re.compile(
    r"^(LEC|TUT|LAB|TST|SEM|DIS|PRJ|FLD)\s+\d+",
    re.IGNORECASE,
)

@dataclass(frozen=True)
class CourseSection:
    class_number: str
    section_number: str
    campus_type: str
    associated_class: str
    enrollment_capacity: int
    enrollment_total: int
    waitlist_capacity: int
    waitlist_total: int
    meeting_time: str
    room: str
    
    @property
    def available_seats(self) -> int:
        return max(self.enrollment_capacity -  self.enrollment_total, 0)
    
    @property
    def appears_open(self) -> bool:
        return self.enrollment_total <  self.enrollment_capacity

def _direct_cells(row: Tag) -> list[str]:
    return [
        cell.get_text(" ", strip=True).replace("\xa0", "").strip()
        for cell in row.find_all("td", recursive=False)
    ]

def _parse_integer(value: str) -> int:
    cleaned = value.strip()

    if not cleaned:
        return 0

    return int(cleaned)

def parse_course_sections(html: str) -> list[CourseSection]:
    soup = BeautifulSoup(html, "html.parser")
    sections: list[CourseSection] = []
    
    for row in soup.find_all("tr"):
        cells = _direct_cells(row)
        if len(cells) < 12:
            continue
        class_number = cells[0]
        section_name = cells[1]
        
        if not class_number.isdigit():
            continue
        if SECTION_PATTERN.fullmatch(section_name) is None:
            continue
            
        try:
            section = CourseSection(
                class_number=class_number,
                section_number=section_name,
                campus_type=cells[2],
                associated_class=cells[3],
                enrollment_capacity=_parse_integer(cells[6]),
                enrollment_total=_parse_integer(cells[7]),
                waitlist_capacity=_parse_integer(cells[8]),
                waitlist_total=_parse_integer(cells[9]),
                meeting_time=cells[10],
                room=cells[11],
            )
        except (IndexError, ValueError):
            continue
        sections.append(section)
    return sections
 
