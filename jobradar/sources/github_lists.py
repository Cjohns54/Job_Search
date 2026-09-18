"""Community new-grad lists on GitHub (SimplifyJobs etc.) that publish a listings.json."""
from __future__ import annotations

from typing import Iterator

from ..http import get_json
from ..models import Job
from ..util import iso_date


def fetch(cfg: dict) -> Iterator[Job]:
    for url in cfg["sources"].get("github_listings", []):
        for j in get_json(url):
            if not j.get("active", True) or j.get("is_visible") is False:
                continue
            locs = j.get("locations") or []
            yield Job(
                company=j.get("company_name", ""), title=j.get("title", ""),
                location="; ".join(locs), url=j.get("url", ""), source="github:simplify",
                posted_at=iso_date(j.get("date_posted")),
                remote=any("remote" in l.lower() for l in locs),
                extra={"sponsorship": j.get("sponsorship")},
            )
