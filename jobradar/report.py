"""Outputs: output/latest.html (morning page), output/jobs.csv (everything), optional Google Sheet."""
from __future__ import annotations

import csv
import datetime as dt
import html
import json
import logging
import os
from pathlib import Path

from .util import today

log = logging.getLogger(__name__)

COLUMNS = ["first_seen", "priority", "fit_score", "company", "title", "location", "region", "flags",
           "why_fit", "concerns", "years_required", "posted_at", "source", "url"]
LABELS = {1: "P1 · Near you, strong fit", 2: "P2 · Top company, strong fit",
          3: "P3 · Near you, partial fit", 4: "P4 · Everything else that passed"}
REGION_NAMES = {"gta_kw": "Toronto/Waterloo", "dc_nova": "DC/NoVA", "remote_ca": "Remote Canada",
                "canada_other": "Canada", "us_other": "US", "us_remote": "Remote US", "unknown": "?"}


def write_csv(rows, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        for r in rows:
            w.writerow([r[c] for c in COLUMNS])


def _row_html(r) -> str:
    e = lambda v: html.escape(str(v or ""))
    flags = "".join(f'<span class="flag">{e(x)}</span>' for x in (r["flags"] or "").split(",") if x)
    return (f'<tr><td class="fit">{e(r["fit_score"])}</td>'
            f'<td><a href="{e(r["url"])}" target="_blank" rel="noopener"><b>{e(r["title"])}</b></a>'
            f'<div class="sub">{e(r["company"])} · {e(r["location"])} {flags}</div>'
            f'<div class="why">{e(r["why_fit"])}{" · ⚠ " + e(r["concerns"]) if r["concerns"] else ""}</div></td>'
            f'<td class="meta">{e(REGION_NAMES.get(r["region"], r["region"]))}<br>{e(r["source"])}<br>{e(r["posted_at"])}</td></tr>')


def write_html(new_rows, recent_rows, path: Path):
    parts = []
    for p in (1, 2, 3, 4):
        group = [r for r in new_rows if r["priority"] == p]
        if group:
            parts.append(f"<h2>{LABELS[p]} <span>({len(group)})</span></h2><table>"
                         + "".join(_row_html(r) for r in group) + "</table>")
    if not parts:
        parts.append("<p class='empty'>No new matches today.</p>")
    older = [r for r in recent_rows if r["priority"] in (1, 2)]
    if older:
        parts.append(f"<details><summary>P1/P2 from the last 7 days ({len(older)})</summary><table>"
                     + "".join(_row_html(r) for r in older) + "</table></details>")
    doc = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Job Radar · {today().isoformat()}</title><style>
:root{{--bg:#fafaf9;--fg:#1c1917;--mut:#78716c;--line:#e7e5e4;--acc:#2563eb;--flag:#fef3c7}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1c1917;--fg:#f5f5f4;--mut:#a8a29e;--line:#44403c;--acc:#60a5fa;--flag:#78350f}}}}
body{{font:15px/1.45 -apple-system,system-ui,sans-serif;background:var(--bg);color:var(--fg);max-width:980px;margin:0 auto;padding:24px 16px}}
h1{{font-size:22px;margin:0 0 4px}}h2{{font-size:16px;margin:28px 0 8px}}h2 span,.sub,.meta,.why{{color:var(--mut)}}
table{{width:100%;border-collapse:collapse}}td{{border-top:1px solid var(--line);padding:10px 8px;vertical-align:top}}
a{{color:var(--acc);text-decoration:none}}.fit{{font-weight:700;width:36px;font-variant-numeric:tabular-nums}}
.meta{{font-size:12px;width:130px;text-align:right}}.sub,.why{{font-size:13px}}
.flag{{background:var(--flag);border-radius:4px;padding:0 5px;margin-left:6px;font-size:11px;color:var(--fg)}}
summary{{cursor:pointer;margin-top:28px;font-weight:600}}</style></head><body>
<h1>Job Radar</h1><div class="sub">{today().strftime('%A, %B %-d')} · {len(new_rows)} new</div>
{''.join(parts)}</body></html>"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(doc, encoding="utf-8")


def push_to_sheet(new_rows):
    creds, sheet_id = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"), os.environ.get("SHEET_ID")
    if not (creds and sheet_id):
        return
    if not new_rows:
        return
    try:
        import gspread
        gc = gspread.service_account_from_dict(json.loads(creds))
        sh = gc.open_by_key(sheet_id)
        try:
            ws = sh.worksheet("Jobs")
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet("Jobs", rows=1000, cols=len(COLUMNS) + 1)
        header = ["status"] + COLUMNS
        if not ws.row_values(1):
            ws.append_row(header)
        ws.append_rows([[""] + [r[c] if r[c] is not None else "" for c in COLUMNS] for r in new_rows],
                       value_input_option="USER_ENTERED")
        log.info("Appended %d rows to Google Sheet", len(new_rows))
    except Exception as e:
        log.warning("Google Sheet update failed: %s", e)


def write_all(store, out_dir: Path):
    d = today().isoformat()
    week_ago = (today() - dt.timedelta(days=7)).isoformat()
    new = store.rows("first_seen = ? AND priority <= 4", (d,))
    recent = store.rows("first_seen < ? AND first_seen >= ? AND priority <= 4", (d, week_ago))
    write_html(new, recent, out_dir / "latest.html")
    write_csv(store.rows("priority <= 4"), out_dir / "jobs.csv")
    push_to_sheet(new)
    return new
