"""Public job-board APIs used by company career sites. No scraping, no login."""
from __future__ import annotations

import datetime as dt
import re
from typing import Iterator

from ..http import get_json, post_json
from ..models import Job
from ..util import iso_date, strip_html, today


def greenhouse(company: str, slug: str, cfg: dict) -> Iterator[Job]:
    data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", params={"content": "true"})
    for j in data.get("jobs", []):
        yield Job(
            company=company, title=j.get("title", ""),
            location=(j.get("location") or {}).get("name", ""),
            url=j.get("absolute_url", ""), source="greenhouse",
            posted_at=iso_date(j.get("first_published") or j.get("updated_at")),
            description=strip_html(j.get("content", "")),
        )


def lever(company: str, slug: str, cfg: dict) -> Iterator[Job]:
    data = get_json(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"})
    for j in data:
        cats = j.get("categories") or {}
        locs = cats.get("allLocations") or [cats.get("location", "")]
        lists = " ".join(f"{x.get('text', '')} {strip_html(x.get('content', ''))}" for x in j.get("lists") or [])
        yield Job(
            company=company, title=j.get("text", ""), location="; ".join(l for l in locs if l),
            url=j.get("hostedUrl", ""), source="lever", posted_at=iso_date(j.get("createdAt")),
            description=f"{j.get('descriptionPlain', '')} {lists}".strip(),
            remote=j.get("workplaceType") == "remote",
        )


def ashby(company: str, slug: str, cfg: dict) -> Iterator[Job]:
    data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location", "")] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
        yield Job(
            company=company, title=j.get("title", ""), location="; ".join(l for l in locs if l),
            url=j.get("jobUrl") or j.get("applyUrl", ""), source="ashby",
            posted_at=iso_date(j.get("publishedAt")),
            description=j.get("descriptionPlain") or strip_html(j.get("descriptionHtml", "")),
            remote=bool(j.get("isRemote")),
        )


def smartrecruiters(company: str, slug: str, cfg: dict) -> Iterator[Job]:
    offset = 0
    while True:
        data = get_json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings",
                        params={"limit": 100, "offset": offset})
        items = data.get("content", [])
        for j in items:
            loc = j.get("location") or {}
            pid = j.get("id")
            yield Job(
                company=company, title=j.get("name", ""),
                location=loc.get("fullLocation") or ", ".join(
                    x for x in [loc.get("city"), loc.get("region"), loc.get("country")] if x),
                url=f"https://jobs.smartrecruiters.com/{slug}/{pid}", source="smartrecruiters",
                posted_at=iso_date(j.get("releasedDate")), remote=bool(loc.get("remote")),
                extra={"enrich": "smartrecruiters",
                       "detail_url": f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{pid}"},
            )
        offset += len(items)
        if not items or offset >= data.get("totalFound", 0) or offset >= 1000:
            break


def _workday_posted(s: str | None) -> str | None:
    if not s:
        return None
    s = s.lower()
    d = today()
    if "today" in s:
        return d.isoformat()
    if "yesterday" in s:
        return (d - dt.timedelta(days=1)).isoformat()
    m = re.search(r"(\d+)\+?\s*days?", s)
    return (d - dt.timedelta(days=int(m.group(1)))).isoformat() if m else None


def workday(company: str, slug: str, cfg: dict) -> Iterator[Job]:
    """slug looks like 'nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite'."""
    host, site = slug.split("/", 1)
    tenant = host.split(".")[0]
    api = f"https://{host}/wday/cxs/{tenant}/{site}"
    cap = cfg["sources"].get("workday_max_per_term", 100)
    seen = set()
    for term in cfg["sources"]["company_search_terms"]:
        offset, total = 0, None
        while offset < cap:
            data = post_json(f"{api}/jobs", {"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": term})
            if total is None:
                total = data.get("total", 0)      # only reliable on the first page
            posts = data.get("jobPostings", [])
            for p in posts:
                path = p.get("externalPath")
                if not path or path in seen:
                    continue
                seen.add(path)
                yield Job(
                    company=company, title=p.get("title", ""), location=p.get("locationsText", ""),
                    url=f"https://{host}/{site}{path}", source="workday",
                    posted_at=_workday_posted(p.get("postedOn")),
                    extra={"enrich": "workday", "detail_url": f"{api}{path}"},
                )
            offset += 20
            if len(posts) < 20 or offset >= total:
                break


def amazon(company: str, slug: str, cfg: dict) -> Iterator[Job]:
    seen = set()
    for country in ("CAN", "USA"):
        for term in cfg["sources"]["company_search_terms"]:
            data = get_json("https://www.amazon.jobs/en/search.json", params={
                "base_query": term, "normalized_country_code[]": country,
                "result_limit": 100, "offset": 0, "sort": "recent"})
            for j in data.get("jobs", []):
                path = j.get("job_path")
                if not path or path in seen:
                    continue
                seen.add(path)
                desc = " ".join(strip_html(j.get(k, "")) for k in
                                ("description", "basic_qualifications", "preferred_qualifications"))
                yield Job(
                    company=company, title=j.get("title", ""),
                    location=j.get("normalized_location") or j.get("location", ""),
                    url=f"https://www.amazon.jobs{path}", source="amazon",
                    posted_at=iso_date(j.get("posted_date")), description=desc,
                )


FETCHERS = {
    "greenhouse": greenhouse, "lever": lever, "ashby": ashby,
    "smartrecruiters": smartrecruiters, "workday": workday, "amazon": amazon,
}


# ---------- lazy detail fetches (only for jobs that survived the cheap filters) ----------
def enrich(job: Job) -> None:
    kind, url = job.extra.get("enrich"), job.extra.get("detail_url")
    if not kind or not url:
        return
    if kind == "workday":
        info = get_json(url).get("jobPostingInfo", {})
        job.description = strip_html(info.get("jobDescription", ""))
        locs = [info.get("location", "")] + list(info.get("additionalLocations") or [])
        if any(locs):
            job.location = "; ".join(l for l in locs if l)
        job.remote = job.remote or "remote" in (info.get("remoteType") or "").lower()
    elif kind == "smartrecruiters":
        secs = (get_json(url).get("jobAd") or {}).get("sections") or {}
        job.description = " ".join(strip_html((v or {}).get("text", "")) for v in secs.values())
