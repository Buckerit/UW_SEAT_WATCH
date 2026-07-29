# All imports for project
from __future__ import annotations
import httpx

WATERLOO_SCHEDULE_URL = (
    "https://classes.uwaterloo.ca/"
    "cgi-bin/cgiwrap/infocour/salook.pl"
)

class WaterlooClientError(RuntimeError):
    pass

async def fetch_course_html(*, level: str, term: str, subject: str, catalog_num: str) -> str: 
    payload = {"level": level.strip().lower(), "sess": term.strip(), "subject": subject.strip().upper(), "cournum": catalog_num.strip().upper(),}
    headers = {"User-Agent": ("UWSeatWatch/0.1 (course availability notifier; contact: arothe995@gmail.com)")}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0),follow_redirects=True) as client: 
            response = await client.post(WATERLOO_SCHEDULE_URL, data=payload, headers=headers)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise WaterlooClientError(
            "Waterloo's Schedule of Classes could not be reached."
        ) from exc
        
    if "Schedule of Classes" not in response.text:
        raise WaterlooClientError(
            "Waterloo returned an unexpected response."
        )
    return response.text
            