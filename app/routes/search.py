from __future__ import annotations
from datetime import date
import re
from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.waterloo.client import fetch_course_html, WaterlooClientError
from app.waterloo.parser import parse_course_sections

router = APIRouter()

templates = Jinja2Templates(directory="app/templates")

TERM_PATTERN = re.compile(r"^\d{4}$")
SUBJECT_PATTERN = re.compile(r"^[A-Za-z]{2,8}$")
CATALOG_PATTERN = re.compile(r"^[A-Za-z0-9-]{1,16}$")
SUBJECT_CODES = """
ACINTY ACTSC ACC AE AFM AMATH ANTH APPLS ARABIC ARBUS ARCH ARCHL ARTS ASL ASTRN
AVIA BASE BE BET BIOL BLKST BME BUS CC CDNST CFM CHE CHEM CHINA CI CIVE CLAS CM
CMW CO COGSCI COMM COMMST CROAT CS CT DAC DATSC DEI DEVP DUTCH EARTH EASIA ECDEV
ECE ECON EDMI EECG EMLS ENBUS ENGL ENVE ENVS ERS FCIT FILM FINE FR GA GBDA GC
GDS GEMCC GENE GEOE GEOG GER GERON GESC GGOV GLOBAL GLST GRK GS GSJ HEALTH HHUM
HIST HLTH HRM HRTS HUMN HUMSC INDENT INDEV INDG INDS INNOV INTEG ITAL ITALST
JAPAN JS KIN KOREA LANG LAT LS MATBUS MATH ME MEDSCI MEDVL MGMT MISC MNS MOHAWK
MSE MTE MTHEL MUSIC NANO NE OPTOM PACS PD PDARCH PDPHRM PHARM PHIL PHYS PLAN
PMATH PS PSCI PSYCH QIC RCS REC REES RELC RSCH RUSS SCBUS SCI SDS SE SEQ SFM SI
SOC SOCWK SPAN SRF STAT STV SUSM SWK SWREN SYDE TAX THPERF TN TPM TS UN UNDC
UNIV UU UX VCULT WATER WIL WKRPT YC
""".split()


def get_default_term_code(today: date | None = None) -> str:
    today = today or date.today()

    if today.month <= 4:
        term_suffix = "5"
        term_year = today.year
    elif today.month <= 8:
        term_suffix = "9"
        term_year = today.year
    else:
        term_suffix = "1"
        term_year = today.year + 1

    return f"1{term_year % 100:02d}{term_suffix}"

  
@router.get("/", response_class=HTMLResponse)
async def show_search_page(request: Request) -> HTMLResponse:
    term = get_default_term_code()

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
                "values": {
                    "level": "under",
                    "term": term,
                    "subject": "AFM",
                    "catalog_number": "",
                },
                "subject_codes": SUBJECT_CODES,
                "errors": [],
            },
    )

@router.post("/search", response_class=HTMLResponse)
async def search_course(request: Request, level: str = Form(...), term: str = Form(...), subject: str = Form(...), catalog_number: str = Form(...)) -> HTMLResponse:
    normalized_level = level.strip().lower()
    normalized_term = term.strip()
    normalized_subject = subject.strip().upper()
    normalized_catalog_number = catalog_number.strip().upper()
    
    values = {
        "level": normalized_level,
        "term": normalized_term,
        "subject": normalized_subject,
        "catalog_number": normalized_catalog_number,
    }
    
    errors: list[str] = []

    if normalized_level not in {"under", "grad"}:
        errors.append("Choose undergraduate or graduate.")

    if TERM_PATTERN.fullmatch(normalized_term) is None:
        errors.append("Term must be a four-digit Waterloo term code.")

    if SUBJECT_PATTERN.fullmatch(normalized_subject) is None or normalized_subject not in SUBJECT_CODES:
        errors.append(
            "Choose a valid Waterloo subject."
        )

    if CATALOG_PATTERN.fullmatch(normalized_catalog_number) is None:
        errors.append(
            "Course number may contain letters, numbers, or a hyphen."
        )

    if errors:
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "values": values,
                "subject_codes": SUBJECT_CODES,
                "errors": errors,
            },
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    
    try: 
        html = await fetch_course_html(
            level=normalized_level,
            catalog_num=catalog_number,
            term=normalized_term,
            subject=normalized_subject,
        )
        sections = parse_course_sections(html)
    except WaterlooClientError:
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "values": values,
                "subject_codes": SUBJECT_CODES,
                "errors": [
                    "Waterloo's Schedule of Classes could not be "
                    "retrieved. Please try again shortly."
                ],
            },
            status_code=status.HTTP_502_BAD_GATEWAY,
        )
    if not sections:
        return templates.TemplateResponse(
            name="index.html",
            request=request,
            context={
                "values": values,
                "subject_codes": SUBJECT_CODES,
                "errors": [
                    "No sections were found for that course and term."
                ],
            },
            status_code=status.HTTP_404_NOT_FOUND,
        )
            
    return templates.TemplateResponse(
        request=request,
        name="results.html",
        context={
            "level": normalized_level,
            "term": normalized_term,
            "subject": normalized_subject,
            "catalog_number": normalized_catalog_number,
            "sections": sections,
        },
    )
