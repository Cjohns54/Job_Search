from __future__ import annotations

import datetime as dt
import hashlib
import html
import math
import re

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def strip_html(s: str | None) -> str:
    if not s:
        return ""
    s = html.unescape(str(s))
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s, flags=re.S | re.I)
    s = _TAG.sub(" ", s)
    return _WS.sub(" ", html.unescape(s)).strip()


def clean(v):
    """pandas NaN / None -> None"""
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def today() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


def iso_date(v) -> str | None:
    v = clean(v)
    if v in (None, ""):
        return None
    if isinstance(v, dt.datetime):
        return v.date().isoformat()
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, (int, float)):
        ts = v / 1000 if v > 1e11 else v          # ms vs seconds
        return dt.datetime.fromtimestamp(ts, dt.timezone.utc).date().isoformat()
    s = str(v).strip()
    if s.isdigit():
        return iso_date(int(s))
    for fmt in ("%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%m/%d/%Y"):
        try:
            return dt.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
    return m.group(1) if m else None


def norm(s: str | None) -> str:
    s = (s or "").lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return _WS.sub(" ", s).strip()


_CO_SUFFIX = re.compile(r"\b(inc|llc|ltd|limited|corp|corporation|co|company|plc|gmbh|ulc)\b")


def norm_company(s: str | None) -> str:
    return _WS.sub(" ", _CO_SUFFIX.sub(" ", norm(s))).strip()


def job_key(company: str, title: str, location: str) -> str:
    t = norm(title)
    t = re.sub(r"\b(20\d\d|new grad|university grad|early career)\b", " ", t)   # same role, relabeled
    t = _WS.sub(" ", t).strip()
    loc = norm(location).split(" ")[0] if location else ""
    raw = f"{norm_company(company)}|{t}|{loc}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]
