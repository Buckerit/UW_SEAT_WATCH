from __future__ import annotations
import re
from dataclasses import dataclass
from bs4 import BeautifulSoup
from bs4.element import Tag

SECTION_PATTERN = re.compile(
    r"^(LEC|TUT|LAB|TST|SEM|DIS|PRJ|FLD)\s+\d+",
    re.IGNORECASE,
)