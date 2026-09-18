"""Fit scoring: LLM when LLM_API_KEY is set (OpenAI or any OpenAI-compatible API like Groq), else keywords."""
from __future__ import annotations

import json
import logging
import os
import re

from .http import SESSION
from .models import Job

log = logging.getLogger(__name__)

SKILLS = ["typescript", "javascript", "react", "next.js", "nextjs", "angular", "node", "python", "aws",
          "sql", "mysql", "llm", "openai", "genai", "generative ai", "agent", "ai", "full stack", "full-stack",
          "rest", "api", "docker", "ci/cd", "cypress", "playwright", "java", "c++"]
JUNIOR = re.compile(r"new grad|university|early career|entry|junior|associate|\b(i|1)\b|graduate", re.I)

PROMPT = """You screen job postings for one candidate. Be realistic, not generous.

CANDIDATE RESUME:
<resume>
__RESUME__
</resume>

CANDIDATE IS LOOKING FOR: __TARGETS__

JOB POSTING:
Company: __COMPANY__
Title: __TITLE__
Location: __LOCATION__
Description:
__DESC__

Reply with JSON only:
{"fit_score": <integer 0-10; 8+ = strong match, candidate likely gets an interview; 5 = plausible stretch; 0-2 = wrong role or far too senior>,
 "why_fit": "<one sentence, max 20 words, the concrete overlap>",
 "years_required": <integer or null>,
 "concerns": "<short phrase: seniority, stack gap, clearance, etc. Empty string if none>"}"""


def heuristic(job: Job) -> tuple[float, str, str]:
    text = f"{job.title} {job.description}".lower()
    hits = [s for s in SKILLS if re.search(r"(?<![a-z])" + re.escape(s) + r"(?![a-z])", text)]
    score = 5.0 + min(len(hits), 6) * 0.5
    if JUNIOR.search(job.title):
        score += 1.5
    if job.years_required is not None and job.years_required >= 3:
        score -= 2
    if not job.description:
        score -= 0.5
    score = max(0.0, min(10.0, score))
    return round(score, 1), ("Keyword match: " + ", ".join(hits[:6])) if hits else "Title match only", ""


class Scorer:
    def __init__(self, cfg: dict, resume: str, use_llm: bool = True):
        self.cfg = cfg
        self.resume = resume
        self.key = os.environ.get("LLM_API_KEY") if use_llm else None
        self.base = os.environ.get("LLM_BASE_URL") or "https://api.openai.com/v1"
        self.model = os.environ.get("LLM_MODEL") or cfg["scoring"]["llm_model"]
        self.budget = cfg["scoring"]["max_llm_jobs_per_run"]
        self.used = 0
        if use_llm and not self.key:
            log.info("LLM_API_KEY not set: using keyword scoring")
        if self.key and not resume:
            log.warning("No resume text found; LLM scoring will be weak. Add resume.txt")

    def score(self, job: Job) -> None:
        if self.key and self.used < self.budget:
            try:
                self.used += 1
                job.fit_score, job.why_fit, job.concerns = self._llm(job)
                return
            except Exception as e:
                log.warning("LLM scoring failed (%s); falling back to keywords", e)
        job.fit_score, job.why_fit, job.concerns = heuristic(job)

    def _llm(self, job: Job) -> tuple[float, str, str]:
        n = self.cfg["scoring"]["description_chars"]
        prompt = (PROMPT.replace("__RESUME__", self.resume)
                  .replace("__TARGETS__", self.cfg["profile"]["targets"])
                  .replace("__COMPANY__", job.company).replace("__TITLE__", job.title)
                  .replace("__LOCATION__", job.location)
                  .replace("__DESC__", (job.description or "(no description available)")[:n]))
        r = SESSION.post(f"{self.base.rstrip('/')}/chat/completions", timeout=60,
                         headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
                         json={"model": self.model, "temperature": 0,
                               "response_format": {"type": "json_object"},
                               "messages": [{"role": "user", "content": prompt}]})
        r.raise_for_status()
        out = json.loads(r.json()["choices"][0]["message"]["content"])
        if out.get("years_required") is not None and job.years_required is None:
            try:
                job.years_required = int(out["years_required"])
            except (TypeError, ValueError):
                pass
        return float(out.get("fit_score", 0)), str(out.get("why_fit", ""))[:200], str(out.get("concerns", ""))[:120]


def assign_priority(job: Job, cfg: dict) -> int:
    p = cfg["priority"]
    fit = job.fit_score or 0
    home = job.region in cfg["home_regions"]
    blocked = "clearance" in job.flags or "us_citizen_only" in job.flags
    if fit < p["min_keep"]:
        return 9                                          # hidden
    if job.watch_group == "past_employer" and fit >= p["partial_fit"]:
        rank = 1 if home else 2
    elif home and fit >= p["good_fit"]:
        rank = 1
    elif job.watch_company and fit >= p["good_fit"]:
        rank = 2
    elif home and fit >= p["partial_fit"]:
        rank = 3
    else:
        rank = 4
    return min(rank + 1, 4) if blocked else rank       # still shown, just nudged down
