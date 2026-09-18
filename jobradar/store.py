"""SQLite state: remembers every job we've looked at so nothing is re-scored or re-shown."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .models import Job
from .util import today

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  key TEXT PRIMARY KEY, url TEXT, first_seen TEXT, posted_at TEXT,
  company TEXT, title TEXT, location TEXT, region TEXT, watch_company TEXT, source TEXT,
  fit_score REAL, why_fit TEXT, concerns TEXT, years_required INTEGER, flags TEXT,
  priority INTEGER, status TEXT DEFAULT 'new',
  contact_name TEXT, contact_title TEXT, contact_url TEXT, contacts_json TEXT, contact_checked TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_url ON jobs(url);
CREATE INDEX IF NOT EXISTS idx_jobs_first_seen ON jobs(first_seen);
"""


class Store:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        have = {r[1] for r in self.db.execute("PRAGMA table_info(jobs)")}
        for col in ("contact_name", "contact_title", "contact_url", "contacts_json", "contact_checked"):
            if col not in have:                                  # upgrade older jobs.db in place
                self.db.execute(f"ALTER TABLE jobs ADD COLUMN {col} TEXT")
        self._keys = {r[0] for r in self.db.execute("SELECT key FROM jobs")}
        self._urls = {r[0] for r in self.db.execute("SELECT url FROM jobs") if r[0]}

    def seen(self, job: Job) -> bool:
        return job.key in self._keys or (job.url and job.url in self._urls)

    def add(self, job: Job, status: str = "new") -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO jobs (key, url, first_seen, posted_at, company, title, location, region, "
            "watch_company, source, fit_score, why_fit, concerns, years_required, flags, priority, status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (job.key, job.url, today().isoformat(), job.posted_at, job.company, job.title, job.location,
             job.region, job.watch_company, job.source, job.fit_score, job.why_fit, job.concerns,
             job.years_required, ",".join(job.flags), job.priority, status))
        self._keys.add(job.key)
        if job.url:
            self._urls.add(job.url)

    def needs_contact(self, limit: int) -> list[sqlite3.Row]:
        return list(self.db.execute(
            "SELECT key, url FROM jobs WHERE priority <= 4 AND contact_checked IS NULL AND url LIKE '%linkedin.com/jobs/view/%' "
            "ORDER BY first_seen DESC, priority ASC LIMIT ?", (limit,)))

    def set_contacts(self, key: str, people: list[dict]) -> None:
        import json
        top = people[0] if people else {}
        self.db.execute(
            "UPDATE jobs SET contact_name=?, contact_title=?, contact_url=?, contacts_json=?, contact_checked=? WHERE key=?",
            (top.get("name"), top.get("title"), top.get("url"), json.dumps(people) if people else None,
             today().isoformat(), key))

    def commit(self):
        self.db.commit()

    def rows(self, where: str = "1=1", params=()) -> list[sqlite3.Row]:
        return list(self.db.execute(
            f"SELECT * FROM jobs WHERE {where} ORDER BY first_seen DESC, priority ASC, fit_score DESC", params))
