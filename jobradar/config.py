from __future__ import annotations

import csv
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_config(path: str | Path | None = None) -> dict:
    with open(path or ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


def load_companies(path: str | Path | None = None) -> list[dict]:
    with open(path or ROOT / "companies.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        names = (r.get("match_names") or r["company"]).split("|")
        r["aliases"] = [n.strip() for n in names if n.strip()]
    return rows


def load_resume(cfg: dict) -> str:
    env = os.environ.get("RESUME_TEXT")
    if env:
        return env
    p = ROOT / cfg["profile"]["resume_text"]
    return p.read_text(encoding="utf-8") if p.exists() else ""
