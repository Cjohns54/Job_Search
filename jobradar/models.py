from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Job:
    company: str
    title: str
    location: str
    url: str
    source: str
    posted_at: str | None = None      # YYYY-MM-DD when known
    description: str = ""
    remote: bool = False
    extra: dict = field(default_factory=dict)

    # filled in by the pipeline
    key: str = ""
    watch_company: str | None = None   # canonical name from companies.csv
    watch_group: str | None = None
    region: str = ""
    years_required: int | None = None
    flags: list[str] = field(default_factory=list)
    fit_score: float | None = None
    why_fit: str = ""
    concerns: str = ""
    priority: int | None = None
