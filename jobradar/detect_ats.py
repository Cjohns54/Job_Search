"""Check every company's configured job board, and look for Greenhouse/Lever/Ashby boards for the custom ones."""
from __future__ import annotations

import re

from .config import load_companies
from .http import SESSION

PROBES = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{s}/jobs",
    "lever": "https://api.lever.co/v0/postings/{s}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{s}",
}


def candidates(c: dict) -> list[str]:
    base = re.sub(r"\(.*?\)", "", c["company"]).lower()
    words = re.findall(r"[a-z0-9]+", base)
    out = ["".join(words), "-".join(words)]
    return [s for i, s in enumerate(out) if s and s not in out[:i]]


def _count(ats: str, slug: str) -> int | None:
    """Number of open jobs on the board, or None if the board doesn't answer."""
    try:
        if ats in PROBES:
            r = SESSION.get(PROBES[ats].format(s=slug), timeout=20)
            if r.status_code != 200:
                return None
            d = r.json()
            return len(d if isinstance(d, list) else d.get("jobs", []))
        if ats == "workday":
            host, site = slug.split("/", 1)
            r = SESSION.post(f"https://{host}/wday/cxs/{host.split('.')[0]}/{site}/jobs",
                             json={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""}, timeout=20)
            return r.json().get("total") if r.status_code == 200 else None
        if ats == "smartrecruiters":
            r = SESSION.get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings",
                            params={"limit": 1}, timeout=20)
            return r.json().get("totalFound") if r.status_code == 200 else None
        if ats == "amazon":
            r = SESSION.get("https://www.amazon.jobs/en/search.json",
                            params={"base_query": "software engineer", "result_limit": 1}, timeout=20)
            return r.json().get("hits") if r.status_code == 200 else None
    except Exception:
        return None
    return None


def detect(check_all: bool = False) -> None:
    print(f"{'COMPANY':<36} {'CONFIGURED':<55} RESULT")
    for c in load_companies():
        conf = f"{c['ats']}:{c['ats_slug']}" if c["ats"] != "custom" else "custom"
        if c["ats"] != "custom":
            n = _count(c["ats"], c["ats_slug"])
            status = f"OK ({n} jobs)" if n else ("EMPTY (0 jobs)" if n == 0 else "BROKEN")
            if n:
                print(f"{c['company']:<36} {conf[:55]:<55} {status}")
                continue
        else:
            status = ""
        hits = [(a, s, n) for s in candidates(c) for a in PROBES if (n := _count(a, s))]
        alt = ", ".join(f"{a}:{s} ({n})" for a, s, n in hits) or "no public board found"
        print(f"{c['company']:<36} {conf[:55]:<55} {status + ' | ' if status else ''}{alt}")
