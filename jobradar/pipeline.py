from __future__ import annotations

import datetime as dt
import logging
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .config import ROOT, load_companies, load_config, load_resume
from .filters import CompanyMatcher, RegionClassifier, TitleFilter, detect_flags, min_years_required
from .models import Job
from .report import write_all
from .scoring import Scorer, assign_priority
from .sources import ats, github_lists, jobspy_source
from .store import Store
from .util import job_key, today

log = logging.getLogger("jobradar")
KEEP_ANYWHERE = {"gta_kw", "dc_nova", "remote_ca"}
KEEP_FOR_WATCH = {"canada_other", "us_other", "us_remote", "unknown"}


def collect(cfg, companies, only: set[str] | None) -> list[Job]:
    src = cfg["sources"]
    want = lambda name: src.get(name) and (only is None or name in only)
    jobs: list[Job] = []
    stats = Counter()

    if want("ats"):
        targets = [c for c in companies if c["ats"] in ats.FETCHERS]
        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = {ex.submit(lambda c=c: list(ats.FETCHERS[c["ats"]](c["company"], c["ats_slug"], cfg))): c
                    for c in targets}
            for f in as_completed(futs):
                c = futs[f]
                try:
                    got = f.result()
                    jobs += got
                    stats[f'{c["ats"]}:{c["company"]}'] = len(got)
                except Exception as e:
                    log.warning("  %-28s %s failed: %s", c["company"], c["ats"], e)
    if want("github"):
        try:
            got = list(github_lists.fetch(cfg))
            jobs += got
            stats["github"] = len(got)
        except Exception as e:
            log.warning("github lists failed: %s", e)
    if want("jobspy_regional"):
        got = list(jobspy_source.regional(cfg))
        jobs += got
        stats["jobspy_regional"] = len(got)
    if want("jobspy_custom_companies"):
        got = list(jobspy_source.custom_companies(cfg, companies))
        jobs += got
        stats["jobspy_custom"] = len(got)
    log.info("Fetched %d raw postings", len(jobs))
    for k, v in sorted(stats.items()):
        log.debug("  %s: %d", k, v)
    return jobs


def run(only: set[str] | None = None, use_llm: bool = True, db_path: Path | None = None,
        out_dir: Path | None = None) -> list:
    cfg = load_config()
    companies = load_companies()
    store = Store(db_path or ROOT / "data" / "jobs.db")
    titles, matcher, regions = TitleFilter(cfg), CompanyMatcher(companies), RegionClassifier(cfg)
    scorer = Scorer(cfg, load_resume(cfg), use_llm=use_llm)
    cutoff = (today() - dt.timedelta(days=cfg["roles"]["max_age_days"])).isoformat()
    funnel = Counter()

    raw = collect(cfg, companies, only)
    batch: dict[str, Job] = {}
    for j in raw:
        funnel["raw"] += 1
        if not j.title or not j.url or not titles.ok(j.title):
            continue
        funnel["title_ok"] += 1
        w = matcher.match(j.company)
        if w:
            j.company, j.watch_company, j.watch_group = w["company"], w["company"], w["group"]
        j.key = job_key(j.company, j.title, j.location)
        if store.seen(j) or j.key in batch:
            funnel["already_seen"] += 1
            continue
        if j.posted_at and j.posted_at < cutoff:
            funnel["too_old"] += 1
            continue
        batch[j.key] = j

    # lazy description fetches, only for survivors
    need = [j for j in batch.values() if j.extra.get("enrich")][: cfg["sources"]["max_detail_fetches"]]
    with ThreadPoolExecutor(max_workers=8) as ex:
        for j, f in [(j, ex.submit(ats.enrich, j)) for j in need]:
            try:
                f.result()
            except Exception as e:
                log.debug("enrich failed %s: %s", j.url, e)

    max_yoe = cfg["roles"]["max_years_required"]
    for j in batch.values():
        j.region = regions.classify(j.location, j.remote)
        keep = j.region in KEEP_ANYWHERE or (j.watch_company and j.region in KEEP_FOR_WATCH)
        j.years_required = min_years_required(j.description)
        j.flags = detect_flags(j.description)
        if not keep:
            funnel["wrong_location"] += 1
            if j.region == "non_na" or not j.watch_company:
                j.priority = 99
                store.add(j, status="filtered")      # remember so we never re-fetch/re-check it
            continue
        if j.years_required is not None and j.years_required > max_yoe:
            funnel["too_senior"] += 1
            j.priority = 99
            store.add(j, status="filtered")
            continue
        scorer.score(j)
        j.priority = assign_priority(j, cfg)
        store.add(j)
        funnel["scored"] += 1
        funnel[f"P{j.priority}"] += 1
    store.commit()

    new = write_all(store, out_dir or ROOT / "output")
    log.info("Funnel: %s", dict(funnel))
    log.info("New today (P1-P4): %d  |  LLM calls: %d  |  tokens in/out: %d/%d", len(new), scorer.used, scorer.tokens_in, scorer.tokens_out)
    return new
