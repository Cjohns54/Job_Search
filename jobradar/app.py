"""Local job tracker: `python -m jobradar app` opens every job with Applied / Ineligible toggles.

Toggles are saved to data/status.json (a plain file, one entry per job). No database server, no login.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import subprocess
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .config import ROOT

DB = ROOT / "data" / "jobs.db"
STATUS = ROOT / "data" / "status.json"
_lock = threading.Lock()


def load_status() -> dict:
    try:
        return json.loads(STATUS.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_status(data: dict) -> None:
    tmp = STATUS.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(STATUS)                       # atomic: never a half-written file


def jobs() -> list[dict]:
    if not DB.exists():
        return []
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    rows = [dict(r) for r in db.execute(
        "SELECT key, url, first_seen, posted_at, company, title, location, region, watch_company, source, "
        "fit_score, why_fit, concerns, flags, priority FROM jobs WHERE priority <= 4")]
    db.close()
    st = load_status()
    for r in rows:
        s = st.get(r["key"], {})
        r["applied"] = bool(s.get("applied"))
        r["ineligible"] = bool(s.get("ineligible"))
    return rows


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):                # keep the terminal quiet
        pass

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/jobs"):
            self._send(200, json.dumps(jobs()).encode(), "application/json")
        elif self.path in ("/", "/index.html"):
            self._send(200, PAGE.encode(), "text/html; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        if self.path != "/api/status":
            return self._send(404, b"not found", "text/plain")
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            key, field, value = body["key"], body["field"], bool(body["value"])
            assert field in ("applied", "ineligible")
        except Exception:
            return self._send(400, b"bad request", "text/plain")
        with _lock:
            st = load_status()
            entry = st.setdefault(key, {})
            entry[field] = value
            entry["updated"] = dt.date.today().isoformat()
            if not entry.get("applied") and not entry.get("ineligible"):
                st.pop(key)                   # keep the file small
            save_status(st)
        self._send(200, b'{"ok":true}', "application/json")


def _git(*args: str) -> bool:
    try:
        r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=60)
        return r.returncode == 0
    except Exception:
        return False


def run(port: int = 8765, pull: bool = True, open_browser: bool = True) -> None:
    if pull:
        print("Pulling latest jobs from GitHub..." + (" done" if _git("pull", "--rebase", "--autostash", "-q") else " skipped (offline or conflict)"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    print(f"Job Radar is open at {url}  (Ctrl+C to quit and back up your statuses)")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if STATUS.exists() and pull:
            _git("add", "data/status.json")
            if not _git("diff", "--cached", "--quiet"):
                ok = _git("commit", "-q", "-m", "Update application statuses") and _git("pull", "--rebase", "-q") and _git("push", "-q")
                print("Statuses backed up to GitHub." if ok else "Statuses saved locally (push skipped).")


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Job Radar</title>
<style>
:root{--bg:#fafaf9;--card:#fff;--fg:#1c1917;--mut:#78716c;--line:#e7e5e4;--acc:#2563eb;--ok:#16a34a;--no:#b45309;--flag:#fef3c7;--chip:#f5f5f4}
@media (prefers-color-scheme:dark){:root{--bg:#171513;--card:#1f1c1a;--fg:#f5f5f4;--mut:#a8a29e;--line:#3a3532;--acc:#60a5fa;--ok:#4ade80;--no:#fbbf24;--flag:#713f12;--chip:#2a2623}}
*{box-sizing:border-box}body{margin:0;font:14px/1.45 -apple-system,system-ui,sans-serif;background:var(--bg);color:var(--fg)}
header{position:sticky;top:0;z-index:2;background:var(--bg);border-bottom:1px solid var(--line);padding:14px 20px 12px}
h1{font-size:18px;margin:0 0 10px;display:flex;gap:12px;align-items:baseline}h1 small{font-weight:400;color:var(--mut);font-size:13px}
.filters{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
select,input[type=search]{font:inherit;color:var(--fg);background:var(--card);border:1px solid var(--line);border-radius:8px;padding:6px 10px}
input[type=search]{min-width:220px;flex:1;max-width:360px}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden}
.seg button{font:inherit;border:0;background:var(--card);color:var(--fg);padding:6px 11px;cursor:pointer;border-right:1px solid var(--line)}
.seg button:last-child{border-right:0}.seg button.on{background:var(--acc);color:#fff}
main{padding:8px 20px 60px;max-width:1200px}
.row{display:grid;grid-template-columns:44px 1fr 150px 190px;gap:14px;align-items:center;padding:12px 4px;border-bottom:1px solid var(--line)}
.row.done{opacity:.55}.fit{font-weight:700;font-variant-numeric:tabular-nums;font-size:15px}
.p{display:inline-block;font-size:11px;border-radius:4px;padding:0 5px;background:var(--chip);color:var(--mut);margin-right:6px}
a.t{color:var(--acc);text-decoration:none;font-weight:600}a.t:hover{text-decoration:underline}
.sub,.why,.meta{color:var(--mut);font-size:12.5px}.meta{text-align:right;line-height:1.5}
.flag{background:var(--flag);border-radius:4px;padding:0 5px;margin-left:6px;font-size:11px;color:var(--fg)}
.tg{display:flex;flex-direction:column;gap:6px;align-items:flex-end}
.sw{display:flex;align-items:center;gap:8px;cursor:pointer;user-select:none;font-size:12.5px;color:var(--mut)}
.sw input{display:none}.sw i{width:34px;height:20px;border-radius:10px;background:var(--line);position:relative;transition:.15s}
.sw i:after{content:"";position:absolute;top:2px;left:2px;width:16px;height:16px;border-radius:50%;background:#fff;transition:.15s;box-shadow:0 1px 2px #0003}
.sw input:checked+i{background:var(--ok)}.sw.inel input:checked+i{background:var(--no)}.sw input:checked+i:after{left:16px}
.sw input:checked~span{color:var(--fg);font-weight:600}
.empty{padding:40px;text-align:center;color:var(--mut)}
@media (max-width:760px){.row{grid-template-columns:36px 1fr;}.meta{display:none}.tg{grid-column:2;flex-direction:row;justify-content:flex-start;gap:18px}}
</style></head><body>
<header><h1>Job Radar <small id="count"></small></h1>
<div class="filters">
 <div class="seg" id="status">
  <button data-v="todo" class="on">To do</button><button data-v="applied">Applied</button>
  <button data-v="notapplied">Not applied</button><button data-v="ineligible">Ineligible</button><button data-v="all">All</button>
 </div>
 <select id="prio"><option value="">All priorities</option><option value="1">P1 near you, strong</option><option value="2">P2 top co, strong</option><option value="3">P3 near you, partial</option><option value="4">P4 other</option></select>
 <select id="region"><option value="">All regions</option><option value="gta_kw">Toronto/Waterloo</option><option value="dc_nova">DC/NoVA</option><option value="remote_ca">Remote Canada</option><option value="canada_other">Other Canada</option><option value="us">US (other/remote)</option></select>
 <select id="age"><option value="">Found: any time</option><option value="0">Found today</option><option value="3">Last 3 days</option><option value="7">Last 7 days</option></select>
 <select id="sort"><option value="new">Newest, then priority</option><option value="fit">Best fit</option><option value="prio">Priority</option></select>
 <input type="search" id="q" placeholder="Search title, company, location">
</div></header>
<main id="list"></main>
<script>
const R={gta_kw:"Toronto/Waterloo",dc_nova:"DC/NoVA",remote_ca:"Remote Canada",canada_other:"Canada",us_other:"US",us_remote:"Remote US",unknown:"?"};
let JOBS=[],st="todo";
const $=s=>document.querySelector(s),esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
function daysAgo(d){return d?Math.floor((Date.now()-new Date(d+"T12:00:00"))/864e5):999}
function view(){
 const p=$("#prio").value,rg=$("#region").value,age=$("#age").value,q=$("#q").value.toLowerCase().trim(),so=$("#sort").value;
 let v=JOBS.filter(j=>{
  if(st==="todo"&&(j.applied||j.ineligible))return false;
  if(st==="applied"&&!j.applied)return false;
  if(st==="notapplied"&&j.applied)return false;
  if(st==="ineligible"&&!j.ineligible)return false;
  if(p&&String(j.priority)!==p)return false;
  if(rg&&(rg==="us"?!(j.region==="us_other"||j.region==="us_remote"):j.region!==rg))return false;
  if(age!==""&&daysAgo(j.first_seen)>+age)return false;
  if(q&&!(j.title+" "+j.company+" "+j.location).toLowerCase().includes(q))return false;
  return true});
 const cmp={new:(a,b)=>(b.first_seen||"").localeCompare(a.first_seen||"")||a.priority-b.priority||(b.fit_score||0)-(a.fit_score||0),
  fit:(a,b)=>(b.fit_score||0)-(a.fit_score||0)||a.priority-b.priority,
  prio:(a,b)=>a.priority-b.priority||(b.fit_score||0)-(a.fit_score||0)}[so];
 v.sort(cmp);
 const ap=JOBS.filter(j=>j.applied).length,ie=JOBS.filter(j=>j.ineligible).length;
 $("#count").textContent=`${v.length} shown · ${JOBS.length} total · ${ap} applied · ${ie} ineligible`;
 $("#list").innerHTML=v.length?v.map(j=>`<div class="row${j.applied||j.ineligible?" done":""}">
  <div class="fit">${j.fit_score??""}</div>
  <div><span class="p">P${j.priority}</span><a class="t" href="${esc(j.url)}" target="_blank" rel="noopener">${esc(j.title)}</a>
   <div class="sub">${esc(j.company)} · ${esc(j.location)}${(j.flags||"").split(",").filter(Boolean).map(f=>`<span class="flag">${esc(f)}</span>`).join("")}</div>
   <div class="why">${esc(j.why_fit)}${j.concerns?" · ⚠ "+esc(j.concerns):""}</div></div>
  <div class="meta">${esc(R[j.region]||j.region)}<br>${esc(j.source)}<br>found ${esc(j.first_seen)}</div>
  <div class="tg">
   <label class="sw"><input type="checkbox" data-k="${j.key}" data-f="applied"${j.applied?" checked":""}><i></i><span>Applied</span></label>
   <label class="sw inel"><input type="checkbox" data-k="${j.key}" data-f="ineligible"${j.ineligible?" checked":""}><i></i><span>Ineligible</span></label>
  </div></div>`).join(""):`<div class="empty">Nothing matches these filters.</div>`;
}
$("#list").addEventListener("change",async e=>{
 const t=e.target;if(!t.dataset.k)return;
 const j=JOBS.find(x=>x.key===t.dataset.k);j[t.dataset.f]=t.checked;
 try{const r=await fetch("/api/status",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({key:j.key,field:t.dataset.f,value:t.checked})});if(!r.ok)throw 0}
 catch{j[t.dataset.f]=!t.checked;alert("Couldn't save. Is the app still running in Terminal?")}
 setTimeout(view,250)});
$("#status").addEventListener("click",e=>{if(!e.target.dataset.v)return;st=e.target.dataset.v;
 document.querySelectorAll("#status button").forEach(b=>b.classList.toggle("on",b===e.target));view()});
["#prio","#region","#age","#sort"].forEach(s=>$(s).addEventListener("change",view));$("#q").addEventListener("input",view);
fetch("/api/jobs").then(r=>r.json()).then(d=>{JOBS=d;view()});
</script></body></html>"""
