"""Probe Greenhouse / Lever / Ashby for each company so companies.csv slugs can be verified or upgraded."""
from __future__ import annotations

import re

from .config import load_companies
from .http import SESSION

PROBES = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{s}/jobs",
    "lever": "https://api.lever.co/v0/postings/{s}?mode=json&limit=1",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{s}",
}


def candidates(c: dict) -> list[str]:
    base = re.sub(r"\(.*?\)", "", c["company"]).lower()
    words = re.findall(r"[a-z0-9]+", base)
    out = ["".join(words), "-".join(words), words[0] if words else ""]
    if c["ats"] in PROBES and c.get("ats_slug"):
        out.insert(0, c["ats_slug"])
    return [s for i, s in enumerate(out) if s and s not in out[:i]]


def probe(ats: str, slug: str) -> int | None:
    try:
        r = SESSION.get(PROBES[ats].format(s=slug), timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        jobs = data if isinstance(data, list) else data.get("jobs", [])
        return len(jobs)
    except Exception:
        return None


def detect(check_all: bool = False) -> None:
    for c in load_companies():
        if not check_all and c["ats"] in ("workday", "amazon", "smartrecruiters") and c["ats_confidence"] != "low":
            continue
        if not check_all and c["ats"] in PROBES and c["ats_confidence"] == "high":
            n = probe(c["ats"], c["ats_slug"])
            print(f"{c['company']:<36} {c['ats']}:{c['ats_slug']:<20} {'OK ' + str(n) + ' jobs' if n is not None else 'NOT FOUND'}")
            continue
        hits = [(a, s, n) for s in candidates(c) for a in PROBES if (n := probe(a, s)) is not None]
        found = ", ".join(f"{a}:{s} ({n} jobs)" for a, s, n in hits) or "none (keep custom)"
        print(f"{c['company']:<36} {found}")
