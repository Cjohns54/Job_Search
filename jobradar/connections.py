"""Your LinkedIn connections, from LinkedIn's data export (Connections.csv or the whole export .zip).

Stored only on this Mac in data/connections.csv (gitignored). Standard library only.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import zipfile
from pathlib import Path

from .config import ROOT, load_companies
from .filters import CompanyMatcher
from .util import norm_company

FILE = ROOT / "data" / "connections.csv"
META = ROOT / "data" / "connections_meta.json"
FIELDS = ["name", "company", "position", "url", "connected_on"]
GENERIC = {"the", "global", "group", "consulting", "solutions", "technologies", "technology", "systems",
           "services", "software", "digital", "labs", "canada", "inc", "self employed", "freelance"}


def parse_export(data: bytes) -> list[dict]:
    """Accepts LinkedIn's Connections.csv or the full export .zip."""
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = [n for n in z.namelist() if n.lower().endswith("connections.csv")]
            if not names:
                raise ValueError("That zip has no Connections.csv inside. Request 'Connections' in the LinkedIn export.")
            data = z.read(names[0])
    text = data.decode("utf-8-sig", errors="replace")
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if l.lower().startswith("first name")), None)
    if start is None:
        raise ValueError("This doesn't look like LinkedIn's Connections.csv (no 'First Name' header).")
    out = []
    for r in csv.DictReader(lines[start:]):
        first, last = (r.get("First Name") or "").strip(), (r.get("Last Name") or "").strip()
        if not (first or last):
            continue
        out.append({"name": f"{first} {last}".strip(), "company": (r.get("Company") or "").strip(),
                    "position": (r.get("Position") or "").strip(), "url": (r.get("URL") or "").strip(),
                    "connected_on": (r.get("Connected On") or "").strip()})
    if not out:
        raise ValueError("Found the header but no connections in the file.")
    return out


def save(rows: list[dict]) -> None:
    FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    META.write_text(json.dumps({"imported": dt.date.today().isoformat(), "count": len(rows)}), encoding="utf-8")


def load() -> list[dict]:
    if not FILE.exists():
        return []
    with open(FILE, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def meta() -> dict:
    try:
        return json.loads(META.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


class Matcher:
    """Finds your connections who currently work at a job's company."""

    def __init__(self, conns: list[dict]):
        try:
            self.watch = CompanyMatcher(load_companies())
        except Exception:
            self.watch = None
        self.by_norm: dict[str, list[dict]] = {}
        self.by_watch: dict[str, list[dict]] = {}
        for c in conns:
            n = norm_company(c.get("company"))
            if not n or n in GENERIC:
                continue
            self.by_norm.setdefault(n, []).append(c)
            w = self.watch.match(c["company"]) if self.watch else None
            if w:
                self.by_watch.setdefault(w["company"], []).append(c)
        self._cache: dict[str, list[dict]] = {}

    def at(self, company: str) -> list[dict]:
        if company in self._cache:
            return self._cache[company]
        j = norm_company(company)
        found: list[dict] = []
        if j and j not in GENERIC:
            w = self.watch.match(company) if self.watch else None
            if w:
                found += self.by_watch.get(w["company"], [])
            found += self.by_norm.get(j, [])
            for k, people in self.by_norm.items():      # "Hays" <-> "Hays Specialist Recruitment"
                if k != j and (k.startswith(j + " ") or (len(k) >= 4 and j.startswith(k + " "))):
                    found += people
        seen, out = set(), []
        for p in found:
            ident = p.get("url") or p["name"]
            if ident not in seen:
                seen.add(ident)
                out.append({k: p.get(k, "") for k in ("name", "position", "company", "url")})
        self._cache[company] = out
        return out
