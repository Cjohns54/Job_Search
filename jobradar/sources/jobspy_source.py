"""LinkedIn / Indeed / Glassdoor / Google Jobs via python-jobspy (no login)."""
from __future__ import annotations

import logging
import time
from typing import Iterator

from ..models import Job
from ..util import clean, iso_date

log = logging.getLogger(__name__)


def _scrape(**kw):
    try:
        from jobspy import scrape_jobs
    except ImportError:
        log.warning("python-jobspy not installed; skipping jobspy sources")
        return []
    try:
        df = scrape_jobs(**kw)
        return df.to_dict("records") if df is not None else []
    except Exception as e:                      # jobspy breaks sometimes; never kill the run
        log.warning("jobspy failed for %s @ %s: %s", kw.get("search_term"), kw.get("location"), e)
        return []


def _to_job(r: dict) -> Job:
    return Job(
        company=str(clean(r.get("company")) or ""), title=str(clean(r.get("title")) or ""),
        location=str(clean(r.get("location")) or ""),
        url=clean(r.get("job_url_direct")) or clean(r.get("job_url")) or "",
        source=f"jobspy:{clean(r.get('site')) or '?'}", posted_at=iso_date(r.get("date_posted")),
        description=str(clean(r.get("description")) or ""), remote=bool(clean(r.get("is_remote"))),
    )


def regional(cfg: dict) -> Iterator[Job]:
    sc = cfg["regional_sweep"]
    for hub in sc["hubs"]:
        for term in sc["search_terms"]:
            rows = _scrape(
                site_name=sc["jobspy_sites"], search_term=term,
                google_search_term=f"{term} jobs near {hub['location']} since yesterday",
                location=hub["location"], distance=hub["distance_miles"], hours_old=sc["hours_old"],
                results_wanted=sc["results_per_query"], country_indeed=hub["country"],
                description_format="markdown", verbose=0)
            for r in rows:
                yield _to_job(r)
            time.sleep(sc.get("sleep_seconds", 4))


def custom_companies(cfg: dict, companies: list[dict]) -> Iterator[Job]:
    """Watch companies whose careers site has no public API: search the boards by name."""
    cc = cfg["custom_company_search"]
    for c in companies:
        if c["ats"] != "custom":
            continue
        for country in cc["countries"]:
            rows = _scrape(
                site_name=["indeed", "linkedin"], search_term=f'"{c["company"]}" software engineer',
                location=country, hours_old=cc["hours_old"], results_wanted=cc["results_per_query"],
                country_indeed=country, description_format="markdown", verbose=0)
            for r in rows:
                yield _to_job(r)                # pipeline keeps only rows whose company matches
            time.sleep(cfg["regional_sweep"].get("sleep_seconds", 4))
