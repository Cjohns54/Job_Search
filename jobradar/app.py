"""Local job tracker: `python -m jobradar app`.

- Applied / Ineligible toggles, saved to data/status.json
- Recruiter ("Meet the hiring team") from the nightly run
- Your LinkedIn connections at each company, from an imported LinkedIn export (stays on this Mac)
Standard library only, so it runs on the Mac's built-in Python.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import subprocess
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote

from . import connections
from .config import ROOT

DB = ROOT / "data" / "jobs.db"
STATUS = ROOT / "data" / "status.json"
SCHOOL_SLUG = "university-of-guelph"
_lock = threading.Lock()


def li_links(company: str) -> dict:
    kw = quote(company)
    base = "https://www.linkedin.com/search/results/people/?keywords="
    return {
        "first": f"{base}{kw}&network=%5B%22F%22%5D&origin=FACETED_SEARCH",
        "second": f"{base}{kw}&network=%5B%22S%22%5D&origin=FACETED_SEARCH",
        "alumni": f"https://www.linkedin.com/school/{SCHOOL_SLUG}/people/?keywords={kw}",
        "recruiters": f"{base}{quote(company + ' recruiter')}&origin=GLOBAL_SEARCH_HEADER",
    }


QUICK_HOSTS = ("greenhouse.io", "grnh.se", "lever.co", "ashbyhq.com", "smartrecruiters.com", "workable.com",
               "applytojob.com", "bamboohr.com", "recruitee.com", "breezy.hr", "rippling.com", "careerpuck.com",
               "recruiterflow.com", "jobvite.com", "teamtailor.com", "pinpointhq.com")
LONG_HOSTS = ("myworkdayjobs.com", "amazon.jobs", "icims.com", "taleo.net", "successfactors", "oraclecloud.com",
              "eightfold.ai", "dayforcehcm.com", "paycomonline", "brassring", "jobs.apple.com", "careers.microsoft.com",
              "metacareers.com", "google.com/about/careers", "avature", "ultipro", "ukg", "adp.com", "ripplehire",
              "phenompeople", "jobs.citi.com", "jobs.scotiabank.com")
EFFORT = {"quick": "⚡ ~2 min", "medium": "~5 min", "long": "🕐 ~15 min · account"}


def effort(url: str, apply_kind: str | None, apply_url: str | None) -> tuple[str, str]:
    """(level, why). Based on where the application form actually lives."""
    if apply_kind == "easy_apply":
        return "quick", "LinkedIn Easy Apply"
    target = (apply_url or url or "").lower()
    if "gh_jid=" in target or any(h in target for h in QUICK_HOSTS):
        return "quick", "Simple form, no account"
    if any(h in target for h in LONG_HOSTS):
        return "long", "Needs an account (Workday, iCIMS, etc.)"
    if "linkedin.com/jobs" in target:
        return "medium", "LinkedIn: apply type not checked yet"
    if "indeed.com" in target:
        return "medium", "Indeed: often Indeed Apply"
    return "medium", "Company careers site"


def load_status() -> dict:
    try:
        return json.loads(STATUS.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_status(data: dict) -> None:
    tmp = STATUS.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(STATUS)


def jobs() -> list[dict]:
    if not DB.exists():
        return []
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    rows = [dict(r) for r in db.execute("SELECT * FROM jobs WHERE priority <= 4")]
    db.close()
    st = load_status()
    conns = connections.load()
    match = connections.Matcher(conns) if conns else None
    for r in rows:
        s = st.get(r["key"], {})
        r["applied"] = bool(s.get("applied"))
        r["ineligible"] = bool(s.get("ineligible"))
        r["referral"] = bool(s.get("referral"))
        try:
            r["people"] = json.loads(r.get("contacts_json") or "[]")
        except json.JSONDecodeError:
            r["people"] = []
        r["conns"] = match.at(r["company"]) if match else []
        r["links"] = li_links(r["company"])
        r["effort"], r["effort_why"] = effort(r["url"], r.get("apply_kind"), r.get("apply_url"))
        for k in ("contacts_json", "description"):
            r.pop(k, None)
    return rows


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj):
        self._send(code, json.dumps(obj).encode())

    def do_GET(self):
        if self.path.startswith("/api/jobs"):
            self._json(200, {"jobs": jobs(), "connections": connections.meta()})
        elif self.path in ("/", "/index.html"):
            self._send(200, PAGE.encode(), "text/html; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        if self.path == "/api/connections":
            if n > 60_000_000:
                return self._json(413, {"error": "File too big. Upload Connections.csv on its own."})
            try:
                rows = connections.parse_export(self.rfile.read(n))
                connections.save(rows)
                return self._json(200, {"count": len(rows)})
            except Exception as e:
                return self._json(400, {"error": str(e)})
        if self.path != "/api/status":
            return self._send(404, b"not found", "text/plain")
        try:
            body = json.loads(self.rfile.read(n))
            key, field, value = body["key"], body["field"], bool(body["value"])
            assert field in ("applied", "ineligible", "referral")
        except Exception:
            return self._send(400, b"bad request", "text/plain")
        with _lock:
            st = load_status()
            entry = st.setdefault(key, {})
            entry[field] = value
            entry["updated"] = dt.date.today().isoformat()
            if not any(entry.get(f) for f in ("applied", "ineligible", "referral")):
                st.pop(key)
            save_status(st)
        self._json(200, {"ok": True})


def _git(*args: str) -> bool:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=60).returncode == 0
    except Exception:
        return False


def run(port: int = 8765, pull: bool = True, open_browser: bool = True) -> None:
    if pull:
        ok = _git("pull", "--rebase", "--autostash", "-q")
        print("Pulling latest jobs from GitHub..." + (" done" if ok else " skipped (offline or conflict)"))
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
                _git("commit", "-q", "-m", "Update application statuses")
            ok = _git("pull", "--rebase", "-q") and _git("push", "-q")
            print("Statuses backed up to GitHub." if ok else "Statuses saved locally (push skipped).")


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Job Radar</title>
<style>
:root{--bg:#fafaf9;--card:#fff;--fg:#1c1917;--mut:#78716c;--line:#e7e5e4;--acc:#2563eb;--ok:#16a34a;--no:#b45309;--flag:#fef3c7;--chip:#f5f5f4;--conn:#dbeafe;--connfg:#1e40af;--rec:#ede9fe;--recfg:#5b21b6}
@media (prefers-color-scheme:dark){:root{--bg:#171513;--card:#1f1c1a;--fg:#f5f5f4;--mut:#a8a29e;--line:#3a3532;--acc:#60a5fa;--ok:#4ade80;--no:#fbbf24;--flag:#713f12;--chip:#2a2623;--conn:#1e3a8a;--connfg:#dbeafe;--rec:#3b0764;--recfg:#ede9fe}}
*{box-sizing:border-box}body{margin:0;font:14px/1.45 -apple-system,system-ui,sans-serif;background:var(--bg);color:var(--fg)}
header{position:sticky;top:0;z-index:2;background:var(--bg);border-bottom:1px solid var(--line);padding:14px 20px 12px}
.top{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:10px}
h1{font-size:18px;margin:0;display:flex;gap:12px;align-items:baseline}h1 small{font-weight:400;color:var(--mut);font-size:13px}
.btn{font:inherit;font-size:13px;border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:8px;padding:6px 12px;cursor:pointer}
.btn:hover{border-color:var(--acc)}.btn small{color:var(--mut);margin-left:6px}
.filters{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
select,input[type=search]{font:inherit;color:var(--fg);background:var(--card);border:1px solid var(--line);border-radius:8px;padding:6px 10px}
input[type=search]{min-width:220px;flex:1;max-width:340px}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden}
.seg button{font:inherit;border:0;background:var(--card);color:var(--fg);padding:6px 11px;cursor:pointer;border-right:1px solid var(--line)}
.seg button:last-child{border-right:0}.seg button.on{background:var(--acc);color:#fff}
main{padding:8px 20px 60px;max-width:1240px}
.row{display:grid;grid-template-columns:40px 1fr 140px 142px;gap:14px;align-items:start;padding:12px 4px;border-bottom:1px solid var(--line)}
.row.done{opacity:.55}.row.ref{border-left:3px solid var(--acc);padding-left:8px;margin-left:-11px}.fit{font-weight:700;font-variant-numeric:tabular-nums;font-size:15px;padding-top:1px}
.p{display:inline-block;font-size:11px;border-radius:4px;padding:0 5px;background:var(--chip);color:var(--mut);margin-right:6px;vertical-align:1px}
.tl{display:flex;flex-wrap:wrap;align-items:center;gap:6px 8px}
a.t{color:var(--acc);text-decoration:none;font-weight:600}a.t:hover{text-decoration:underline}
.chip{font:inherit;font-size:12px;border:0;border-radius:999px;padding:1px 9px;cursor:pointer;text-decoration:none;white-space:nowrap}
.chip.c{background:var(--conn);color:var(--connfg)}.chip.r{background:var(--rec);color:var(--recfg);max-width:420px;overflow:hidden;text-overflow:ellipsis}
.chip.e{cursor:default;border:1px solid var(--line);background:transparent;color:var(--mut)}.chip.e.quick{border-color:var(--ok);color:var(--ok)}.chip.e.long{border-color:var(--no);color:var(--no)}
.chip.c0{background:var(--chip);color:var(--mut);cursor:default}
.sub,.why,.meta,.links{color:var(--mut);font-size:12.5px}.meta{text-align:right;line-height:1.5}
.links a{color:var(--mut);text-decoration:none;margin-right:12px}.links a:hover{color:var(--acc)}
.flag{background:var(--flag);border-radius:4px;padding:0 5px;margin-left:6px;font-size:11px;color:var(--fg)}
.panel{margin-top:8px;border:1px solid var(--line);border-radius:10px;background:var(--card);padding:10px 12px}
.panel h4{margin:0 0 6px;font-size:12.5px}.panel .note{color:var(--mut);font-size:12px;margin-top:6px}
.person{display:grid;grid-template-columns:minmax(140px,220px) 1fr;gap:10px;padding:4px 0;border-top:1px solid var(--line);font-size:13px}
.person:first-of-type{border-top:0}.person a{color:var(--acc);text-decoration:none;font-weight:600}.person span{color:var(--mut)}
.tg{display:flex;flex-direction:column;gap:6px;align-items:flex-end}
.sw{display:flex;align-items:center;gap:8px;cursor:pointer;user-select:none;font-size:12.5px;color:var(--mut)}
.sw input{display:none}.sw i{width:34px;height:20px;border-radius:10px;background:var(--line);position:relative;transition:.15s;flex:none}
.sw i:after{content:"";position:absolute;top:2px;left:2px;width:16px;height:16px;border-radius:50%;background:#fff;transition:.15s;box-shadow:0 1px 2px #0003}
.sw input:checked+i{background:var(--ok)}.sw.inel input:checked+i{background:var(--no)}.sw.ref input:checked+i{background:var(--acc)}.sw input:checked+i:after{left:16px}
.sw input:checked~span{color:var(--fg);font-weight:600}
.empty{padding:40px;text-align:center;color:var(--mut)}
dialog{border:1px solid var(--line);border-radius:14px;background:var(--card);color:var(--fg);width:min(520px,92vw);padding:20px}
dialog::backdrop{background:#0006}dialog h2{margin:0 0 6px;font-size:17px}dialog ol{padding-left:18px;color:var(--mut);font-size:13px}
.drop{border:2px dashed var(--line);border-radius:12px;padding:28px;text-align:center;color:var(--mut);cursor:pointer;margin:12px 0}
.drop.over{border-color:var(--acc);color:var(--acc)}.msg{font-size:13px;min-height:18px}.msg.err{color:#dc2626}.msg.ok{color:var(--ok)}
@media (max-width:760px){.row{grid-template-columns:34px 1fr}.meta{display:none}.tg{grid-column:2;flex-direction:row;justify-content:flex-start;gap:18px}}
</style></head><body>
<header>
 <div class="top"><h1>Job Radar <small id="count"></small></h1>
  <button class="btn" id="imp">Import LinkedIn connections<small id="cmeta"></small></button></div>
 <div class="filters">
  <div class="seg" id="status">
   <button data-v="todo" class="on">To do</button><button data-v="applied">Applied</button>
   <button data-v="notapplied">Not applied</button><button data-v="ineligible">Ineligible</button><button data-v="referral">Referral list</button><button data-v="all">All</button>
  </div>
  <select id="prio"><option value="">All priorities</option><option value="1">P1 near you, strong</option><option value="2">P2 top co, strong</option><option value="3">P3 near you, partial</option><option value="4">P4 other</option></select>
  <select id="region"><option value="">All regions</option><option value="gta_kw">Toronto/Waterloo</option><option value="dc_nova">DC/NoVA</option><option value="remote_ca">Remote Canada</option><option value="canada_other">Other Canada</option><option value="us">US (other/remote)</option></select>
  <select id="age"><option value="">Found: any time</option><option value="0">Found today</option><option value="3">Last 3 days</option><option value="7">Last 7 days</option></select>
  <select id="eff"><option value="">Any effort</option><option value="quick">⚡ Quick (~2 min)</option><option value="medium">Medium (~5 min)</option><option value="long">Account needed (~15 min)</option><option value="notlong">Quick + medium</option></select>
  <select id="sort"><option value="new">Newest, then priority</option><option value="fit">Best fit</option><option value="prio">Priority</option><option value="conns">Most connections</option><option value="effort">Quickest first</option></select>
  <label class="sw"><input type="checkbox" id="hasc"><i></i><span>Has connections</span></label>
  <label class="sw"><input type="checkbox" id="hasp"><i></i><span>Has recruiter</span></label>
  <input type="search" id="q" placeholder="Search title, company, location, people">
 </div>
</header>
<main id="list"></main>
<dialog id="dlg"><h2>Import LinkedIn connections</h2>
 <ol><li>LinkedIn → Settings → Data privacy → <b>Get a copy of your data</b></li>
  <li>Pick <b>Connections</b> (or the full archive) and request it</li>
  <li>Drop the emailed <b>.zip</b> or <b>Connections.csv</b> below. Re-import anytime to refresh.</li></ol>
 <div class="drop" id="drop">Drag the file here, or click to choose<input type="file" id="file" accept=".csv,.zip" hidden></div>
 <div class="msg" id="msg">Stays on this Mac only. It's never uploaded to GitHub.</div>
 <div style="text-align:right;margin-top:10px"><button class="btn" onclick="dlg.close()">Close</button></div>
</dialog>
<script>
const ER={quick:0,medium:1,long:2};const EF={quick:"⚡ ~2 min",medium:"~5 min",long:"🕐 ~15 min · account"};
const R={gta_kw:"Toronto/Waterloo",dc_nova:"DC/NoVA",remote_ca:"Remote Canada",canada_other:"Canada",us_other:"US",us_remote:"Remote US",unknown:"?"};
let JOBS=[],CMETA={},st="todo";const open=new Set();
const $=s=>document.querySelector(s),dlg=$("#dlg"),esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
function daysAgo(d){return d?Math.floor((Date.now()-new Date(d+"T12:00:00"))/864e5):999}
function person(p,sub){return `<div class="person"><div>${p.url?`<a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.name)}</a>`:esc(p.name)}</div><span>${esc(sub)}</span></div>`}
function row(j){
 const nc=(j.conns||[]).length,rec=(j.people||[])[0],L=j.links;
 const cchip=CMETA.count?(nc?`<button class="chip c" data-open="${j.key}">🤝 ${nc} connection${nc>1?"s":""}</button>`:`<span class="chip c0">0 connections</span>`):"";
 const rchip=rec?`<a class="chip r" href="${esc(rec.url||L.recruiters)}" target="_blank" rel="noopener" title="${esc(rec.name)} · ${esc(rec.title)}">👤 ${esc(rec.name)}${rec.title?" · "+esc(rec.title):""}</a>`:"";
 const panel=open.has(j.key)?`<div class="panel">
   ${nc?`<h4>Your connections at ${esc(j.company)}</h4>${j.conns.map(p=>person(p,[p.position,p.company].filter(Boolean).join(" · "))).join("")}
   <div class="note">LinkedIn's export doesn't include schools. Click a name to see theirs, or check <a href="${esc(L.alumni)}" target="_blank" rel="noopener">Guelph alumni at ${esc(j.company)}</a>.</div>`:""}
   ${(j.people||[]).length?`<h4 style="margin-top:10px">Hiring team</h4>${j.people.map(p=>person(p,p.title)).join("")}`:""}
  </div>`:"";
 return `<div class="row${j.applied||j.ineligible?" done":""}${j.referral&&!j.applied?" ref":""}">
  <div class="fit">${j.fit_score??""}</div>
  <div><div class="tl"><span><span class="p">P${j.priority}</span><a class="t" href="${esc(j.url)}" target="_blank" rel="noopener">${esc(j.title)}</a></span><span class="chip e ${j.effort}" title="${esc(j.effort_why)}">${EF[j.effort]}</span>${cchip}${rchip}</div>
   <div class="sub">${esc(j.company)} · ${esc(j.location)}${(j.flags||"").split(",").filter(Boolean).map(f=>`<span class="flag">${esc(f)}</span>`).join("")}</div>
   <div class="why">${esc(j.why_fit)}${j.concerns?" · ⚠ "+esc(j.concerns):""}</div>
   <div class="links">${j.apply_url?`<a href="${esc(j.apply_url)}" target="_blank" rel="noopener"><b>Apply form ↗</b></a>`:""}<a href="${esc(L.first)}" target="_blank" rel="noopener">1st-degree ↗</a><a href="${esc(L.second)}" target="_blank" rel="noopener">2nd-degree ↗</a><a href="${esc(L.alumni)}" target="_blank" rel="noopener">Guelph alumni ↗</a><a href="${esc(L.recruiters)}" target="_blank" rel="noopener">Recruiters ↗</a></div>
   ${panel}</div>
  <div class="meta">${esc(R[j.region]||j.region)}<br>${esc(j.source)}<br>found ${esc(j.first_seen)}</div>
  <div class="tg">
   <label class="sw"><input type="checkbox" data-k="${j.key}" data-f="applied"${j.applied?" checked":""}><i></i><span>Applied</span></label>
   <label class="sw inel"><input type="checkbox" data-k="${j.key}" data-f="ineligible"${j.ineligible?" checked":""}><i></i><span>Ineligible</span></label>
   <label class="sw ref"><input type="checkbox" data-k="${j.key}" data-f="referral"${j.referral?" checked":""}><i></i><span>Get referral</span></label>
  </div></div>`}
function view(){
 const p=$("#prio").value,rg=$("#region").value,age=$("#age").value,q=$("#q").value.toLowerCase().trim(),so=$("#sort").value;
 let v=JOBS.filter(j=>{
  if(st==="todo"&&(j.applied||j.ineligible))return false;
  if(st==="applied"&&!j.applied)return false;
  if(st==="notapplied"&&j.applied)return false;
  if(st==="ineligible"&&!j.ineligible)return false;
  if(st==="referral"&&!j.referral)return false;
  if(p&&String(j.priority)!==p)return false;
  if(rg&&(rg==="us"?!(j.region==="us_other"||j.region==="us_remote"):j.region!==rg))return false;
  if(age!==""&&daysAgo(j.first_seen)>+age)return false;
  const ef=$("#eff").value;if(ef&&(ef==="notlong"?j.effort==="long":j.effort!==ef))return false;
  if($("#hasc").checked&&!(j.conns||[]).length)return false;
  if($("#hasp").checked&&!(j.people||[]).length)return false;
  if(q&&!(j.title+" "+j.company+" "+j.location+" "+[...(j.people||[]),...(j.conns||[])].map(x=>x.name).join(" ")).toLowerCase().includes(q))return false;
  return true});
 const f=(a,b)=>(b.fit_score||0)-(a.fit_score||0);
 v.sort({new:(a,b)=>(b.first_seen||"").localeCompare(a.first_seen||"")||a.priority-b.priority||f(a,b),
  fit:(a,b)=>f(a,b)||a.priority-b.priority,prio:(a,b)=>a.priority-b.priority||f(a,b),
  conns:(a,b)=>(b.conns||[]).length-(a.conns||[]).length||a.priority-b.priority,
  effort:(a,b)=>ER[a.effort]-ER[b.effort]||a.priority-b.priority||f(a,b)}[so]);
 const ap=JOBS.filter(j=>j.applied).length,ie=JOBS.filter(j=>j.ineligible).length,rf=JOBS.filter(j=>j.referral&&!j.applied).length;
 $("#count").textContent=`${v.length} shown · ${JOBS.length} total · ${ap} applied · ${rf} chasing a referral · ${ie} ineligible`;
 $("#cmeta").textContent=CMETA.count?`${CMETA.count} imported ${CMETA.imported}`:"not imported yet";
 $("#list").innerHTML=v.length?v.map(row).join(""):`<div class="empty">Nothing matches these filters.</div>`;
}
function load(){return fetch("/api/jobs").then(r=>r.json()).then(d=>{JOBS=d.jobs;CMETA=d.connections||{};view()})}
$("#list").addEventListener("click",e=>{const b=e.target.closest("[data-open]");if(!b)return;
 const k=b.dataset.open;open.has(k)?open.delete(k):open.add(k);view()});
$("#list").addEventListener("change",async e=>{
 const t=e.target;if(!t.dataset.k)return;
 const j=JOBS.find(x=>x.key===t.dataset.k);j[t.dataset.f]=t.checked;
 try{const r=await fetch("/api/status",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({key:j.key,field:t.dataset.f,value:t.checked})});if(!r.ok)throw 0}
 catch{j[t.dataset.f]=!t.checked;alert("Couldn't save. Is the app still running in Terminal?")}
 setTimeout(view,250)});
$("#status").addEventListener("click",e=>{if(!e.target.dataset.v)return;st=e.target.dataset.v;
 if(st==="referral")$("#sort").value="conns";                      // most connections first
 document.querySelectorAll("#status button").forEach(b=>b.classList.toggle("on",b===e.target));view()});
["#prio","#region","#age","#eff","#sort","#hasc","#hasp"].forEach(s=>$(s).addEventListener("change",view));$("#q").addEventListener("input",view);
// import dialog
const drop=$("#drop"),file=$("#file"),msg=$("#msg");
$("#imp").onclick=()=>{msg.className="msg";msg.textContent="Stays on this Mac only. It's never uploaded to GitHub.";dlg.showModal()};
drop.onclick=()=>file.click();file.onchange=()=>file.files[0]&&upload(file.files[0]);
["dragenter","dragover"].forEach(ev=>drop.addEventListener(ev,e=>{e.preventDefault();drop.classList.add("over")}));
["dragleave","drop"].forEach(ev=>drop.addEventListener(ev,e=>{e.preventDefault();drop.classList.remove("over")}));
drop.addEventListener("drop",e=>e.dataTransfer.files[0]&&upload(e.dataTransfer.files[0]));
async function upload(f){msg.className="msg";msg.textContent=`Reading ${f.name}...`;
 try{const r=await fetch("/api/connections",{method:"POST",body:f});const d=await r.json();
  if(!r.ok)throw new Error(d.error||"Upload failed");
  await load();const hits=JOBS.filter(j=>(j.conns||[]).length).length;
  msg.className="msg ok";msg.textContent=`Imported ${d.count} connections. You know someone at ${hits} of these jobs.`}
 catch(e){msg.className="msg err";msg.textContent=e.message}}
load();
</script></body></html>"""
