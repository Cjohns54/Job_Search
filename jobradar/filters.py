"""Cheap, deterministic filters that run before any LLM call."""
from __future__ import annotations

import re

from .util import norm, norm_company


# ---------- titles ----------
def _kw_regex(words: list[str]) -> re.Pattern:
    parts = [re.escape(w.lower().strip()) for w in words if w.strip()]
    return re.compile(r"(?<![a-z0-9])(" + "|".join(parts) + r")(?![a-z0-9])")


class TitleFilter:
    def __init__(self, cfg: dict):
        self.inc = _kw_regex(cfg["roles"]["include_title_keywords"])
        self.exc = _kw_regex(cfg["roles"]["exclude_title_keywords"])

    def ok(self, title: str) -> bool:
        t = " " + re.sub(r"[^a-z0-9.+#/-]+", " ", (title or "").lower()) + " "
        t_for_exc = t.replace("member of technical staff", "mts")
        if self.exc.search(t_for_exc):
            return False
        return bool(self.inc.search(t)) or " mts " in t_for_exc


# ---------- companies ----------
class CompanyMatcher:
    def __init__(self, companies: list[dict]):
        self.entries = []
        for c in companies:
            for a in c["aliases"]:
                self.entries.append((norm_company(a), c))
        self.entries.sort(key=lambda e: -len(e[0]))      # longest alias first

    def match(self, company: str) -> dict | None:
        n = norm_company(company)
        if not n:
            return None
        for alias, c in self.entries:
            if n == alias or n.startswith(alias + " "):
                return c
        return None


# ---------- locations ----------
US_STATES = {
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id", "il", "in", "ia", "ks", "ky",
    "la", "me", "md", "ma", "mi", "mn", "ms", "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd",
    "oh", "ok", "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv", "wi", "wy", "dc"}
US_WORDS = ["united states", "usa", "u.s.", "us remote", "remote us", "remote - us", "(us)", "new york", "nyc",
            "san francisco", "sf", "seattle", "california", "texas", "massachusetts", "boston", "austin",
            "chicago", "bay area", "los angeles", "denver", "atlanta", "washington", "virginia"]
CA_WORDS = ["canada", "ontario", "british columbia", "quebec", "alberta", "vancouver", "montreal",
            "ottawa", "calgary", "edmonton", "winnipeg", "halifax"]
CA_PROV = {"on", "bc", "qc", "ab", "mb", "ns", "nb", "sk", "nl", "pe"}
NON_NA = ["united kingdom", "london", "uk", "ireland", "dublin", "germany", "berlin", "munich", "france",
          "paris", "india", "bangalore", "bengaluru", "hyderabad", "pune", "singapore", "japan", "tokyo",
          "australia", "sydney", "israel", "tel aviv", "netherlands", "amsterdam", "poland", "spain",
          "brazil", "mexico", "china", "taiwan", "korea", "philippines", "emea", "apac", "latam"]


def _has_any(s: str, words) -> bool:
    return any(re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", s) for w in words)


def _abbr_tokens(loc: str) -> set[str]:
    return set(re.findall(r"(?:,|\s-)\s*([A-Za-z]{2})\b", loc))


class RegionClassifier:
    def __init__(self, cfg: dict):
        self.regions = cfg["regions"]

    def classify(self, location: str, remote: bool = False) -> str:
        raw = location or ""
        l = raw.lower()
        for name, r in self.regions.items():
            if _has_any(l, r.get("cities_unambiguous", [])):
                return name
            if _has_any(l, r.get("cities_need_state", [])) and any(t in l + " " for t in r["state_tokens"]):
                return name
        abbr = {a.lower() for a in _abbr_tokens(raw)}
        is_ca = _has_any(l, CA_WORDS) or bool(abbr & CA_PROV)
        is_us = _has_any(l, US_WORDS) or bool(abbr & US_STATES)
        is_remote = remote or "remote" in l
        if is_remote and is_ca:
            return "remote_ca"
        if is_ca:
            return "canada_other"
        if is_us:
            return "us_remote" if is_remote else "us_other"
        if _has_any(l, NON_NA):
            return "non_na"
        if is_remote or not l.strip() or "locations" in l or "multiple" in l:
            return "unknown"        # "Remote", "2 Locations", blank: keep for watch companies
        return "unknown"


# ---------- description checks ----------
_YOE = re.compile(r"(\d{1,2})\s*(?:\+|plus)?\s*(?:(?:-|to|–)\s*\d{1,2}\s*\+?\s*)?(?:years|yrs)", re.I)


def min_years_required(desc: str) -> int | None:
    if not desc:
        return None
    found = []
    for m in _YOE.finditer(desc):
        after = desc[m.end(): m.end() + 90].lower()
        before = desc[max(0, m.start() - 50): m.start()].lower()
        if "experience" in after or "experience" in before:
            n = int(m.group(1))
            if 0 < n <= 20:
                found.append(n)
    return min(found) if found else None


FLAG_PATTERNS = {
    "clearance": r"security clearance|clearance required|active clearance|ts/sci|top secret|secret clearance|public trust|polygraph",
    "us_citizen_only": r"u\.?s\.? citizen(ship)? (is )?required|must be a u\.?s\.? citizen|u\.?s\.? persons? only|must be a u\.?s\.? person",
    "no_sponsorship": r"(not|unable to|cannot|won't|will not) (currently )?(provide |offer |support )?(visa )?sponsor|without (the need for )?(current or future )?(visa )?sponsorship|no (visa )?sponsorship",
}
_FLAGS = {k: re.compile(v, re.I) for k, v in FLAG_PATTERNS.items()}


def detect_flags(desc: str) -> list[str]:
    return [k for k, rx in _FLAGS.items() if desc and rx.search(desc)]
