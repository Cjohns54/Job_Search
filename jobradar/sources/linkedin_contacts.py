"""'Meet the hiring team' from LinkedIn's public (logged-out) job page.

Only works for jobs found on LinkedIn, and only when LinkedIn shows the poster to logged-out visitors.
Never uses your LinkedIn account.
"""
from __future__ import annotations

import html
import re

from ..http import SESSION

GUEST_API = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{id}"


def job_id(url: str) -> str | None:
    m = re.search(r"linkedin\.com/jobs/view/(?:[^/?]*-)?(\d{6,})", url or "") or \
        re.search(r"currentJobId=(\d{6,})", url or "")
    return m.group(1) if m else None


def _text(s: str | None) -> str:
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or ""))).strip()


def parse(page: str) -> list[dict]:
    """Return [{name, title, url}] for everyone in the hiring-team block."""
    start = page.find("message-the-recruiter")
    if start < 0:
        i = page.lower().find("meet the hiring team")
        start = i
    if start < 0:
        return []
    block = page[start:start + 8000]
    people = []
    cards = re.split(r'(?=<div[^>]*class="[^"]*base-card[^""]*")', block)
    for card in cards:
        name = re.search(r'class="[^"]*base-main-card__title[^"]*"[^>]*>(.*?)</h\d>', card, re.S)
        if not name:
            name = re.search(r'<span class="sr-only">(.*?)</span>', card, re.S)
        if not name or not _text(name.group(1)):
            continue
        title = re.search(r'class="[^"]*base-main-card__subtitle[^"]*"[^>]*>(.*?)</h\d>', card, re.S)
        link = re.search(r'href="(https://[a-z]{2,3}\.linkedin\.com/in/[^"?]+)', card)
        p = {"name": _text(name.group(1)), "title": _text(title.group(1)) if title else "",
             "url": link.group(1) if link else ""}
        if p["name"] not in {x["name"] for x in people}:
            people.append(p)
        if len(people) >= 3:
            break
    return people


def fetch(url: str) -> list[dict] | None:
    """None = couldn't check (not LinkedIn / blocked); [] = checked, nobody listed."""
    jid = job_id(url)
    if not jid:
        return None
    r = SESSION.get(GUEST_API.format(id=jid), timeout=20, headers={"Accept": "text/html"})
    if r.status_code == 429:
        raise RuntimeError("rate limited")
    if r.status_code != 200:
        return None
    return parse(r.text)


def people_search_url(company: str) -> str:
    from urllib.parse import quote
    return ("https://www.linkedin.com/search/results/people/?keywords="
            + quote(f"{company} recruiter") + "&origin=GLOBAL_SEARCH_HEADER")
