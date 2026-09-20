"""Knowledge base ("About me") + referral email / resume tailoring, all local and stdlib-only.

Files (all gitignored, they never leave this Mac):
  data/knowledge.json  your answers, stories and notes
  data/settings.json   API key, model, writing voice
  data/generated.json  cached emails and tailored resumes per job
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from io import BytesIO

from .config import ROOT

KB = ROOT / "data" / "knowledge.json"
SETTINGS = ROOT / "data" / "settings.json"
CACHE = ROOT / "data" / "generated.json"
RESUME = ROOT / "resume.txt"
DEFAULT_MODEL = "gpt-5-mini"


# ---------- tiny json file helpers ----------
def _read(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(path)


def entries() -> list[dict]:
    return _read(KB, [])


def settings() -> dict:
    s = _read(SETTINGS, {})
    s.setdefault("model", DEFAULT_MODEL)
    s.setdefault("voice", "third")          # third | first
    return s


def save_settings(patch: dict) -> dict:
    s = settings()
    for k in ("api_key", "model", "voice", "phone", "email", "linkedin", "github"):
        if k in patch:
            s[k] = (patch[k] or "").strip()
    _write(SETTINGS, s)
    return public_settings()


def public_settings() -> dict:
    s = settings()
    return {"has_key": bool(s.get("api_key")), "model": s.get("model"), "voice": s.get("voice"),
            "phone": s.get("phone", ""), "email": s.get("email", ""),
            "linkedin": s.get("linkedin", ""), "github": s.get("github", "")}


def add_entry(title: str, text: str) -> list[dict]:
    text = (text or "").strip()
    if not text:
        raise ValueError("Nothing to save: the text was empty.")
    rows = entries()
    rows.insert(0, {"id": uuid.uuid4().hex[:8], "title": (title or "Note").strip()[:80],
                    "text": text[:40000], "added": time.strftime("%Y-%m-%d")})
    _write(KB, rows)
    return rows


def update_entry(eid: str, title: str | None, text: str | None) -> list[dict]:
    rows = entries()
    for r in rows:
        if r["id"] == eid:
            if title is not None:
                r["title"] = title.strip()[:80]
            if text is not None:
                r["text"] = text.strip()[:40000]
    _write(KB, rows)
    return rows


def delete_entry(eid: str) -> list[dict]:
    rows = [r for r in entries() if r["id"] != eid]
    _write(KB, rows)
    return rows


# ---------- file text extraction ----------
def extract_text(name: str, data: bytes) -> str:
    low = (name or "").lower()
    if low.endswith((".txt", ".md", ".csv", ".json")):
        return data.decode("utf-8", errors="replace")
    if low.endswith(".docx"):
        with zipfile.ZipFile(BytesIO(data)) as z:
            xml = z.read("word/document.xml").decode("utf-8", errors="replace")
        xml = re.sub(r"</w:p>", "\n", xml)
        return re.sub(r"\s*\n\s*", "\n", re.sub(r"<[^>]+>", "", xml)).strip()
    if low.endswith(".pdf"):
        return _pdf_text(data)
    return data.decode("utf-8", errors="replace")


def _pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        try:
            from PyPDF2 import PdfReader                    # type: ignore
        except ImportError:
            raise ValueError("To import PDFs run: pip3 install --user pypdf   "
                             "(or paste the text into the box instead)")
    text = "\n".join((p.extract_text() or "") for p in PdfReader(BytesIO(data)).pages)
    if not text.strip():
        raise ValueError("That PDF has no selectable text (it's probably a scan). Paste the text instead.")
    return text


# ---------- the model ----------
def _chat(prompt: str, max_tokens: int = 1200) -> str:
    s = settings()
    key = s.get("api_key")
    if not key:
        raise ValueError("Add your OpenAI API key in the About me panel first.")
    model = s.get("model") or DEFAULT_MODEL
    base = (s.get("base_url") or "https://api.openai.com/v1").rstrip("/")
    body = {"model": model, "messages": [{"role": "user", "content": prompt}]}
    body.update({"reasoning_effort": "low"} if model.startswith("gpt-5") else {"temperature": 0.4})

    def post(payload):
        req = urllib.request.Request(f"{base}/chat/completions", method="POST",
                                     data=json.dumps(payload).encode(),
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode())

    try:
        out = post(body)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        if e.code == 400:
            out = post({"model": model, "messages": body["messages"]})
        elif e.code in (401, 403):
            raise ValueError("The API key was rejected. Check it in the About me panel.")
        elif e.code == 404:
            raise ValueError(f"Model '{model}' isn't available on this key. Change it in the About me panel.")
        else:
            raise ValueError(f"OpenAI error {e.code}: {detail}")
    return out["choices"][0]["message"]["content"].strip()


def _context(limit: int = 12000) -> str:
    parts = []
    if RESUME.exists():
        parts.append("RESUME:\n" + RESUME.read_text(encoding="utf-8"))
    for e in entries():
        parts.append(f"NOTE — {e['title']}:\n{e['text']}")
    text = "\n\n".join(parts)
    return text[:limit]


EMAIL_PROMPT = """Write a short referral-request email for this exact job. Plain text, no markdown, no subject line beyond the one you output on the first line as "Subject: ...".

STRUCTURE, in this order:
1. "Hi [Name],"
2. One or two sentences: who he is. Connor Johnson, Computer Engineering graduate from the University of Guelph (2025), about __YEARS__ of software engineering experience.
3. One sentence showing he knows who they are: their role at the company, and that he came across them on LinkedIn.
4. The role he is writing about, with the job title and the link (and job ID if one is in the link).
5. A note of 100 to 200 words, written __VOICE__, on why he fits THIS role: relevant experience, skills and concrete achievements drawn only from the context below. Specific, no fluff, no invented facts.
6. A short closing line asking if they would be open to referring him for that specific role, then "Thanks,\\nConnor Johnson" and his contact lines if provided.

HARD RULES:
- Never ask to be referred "to any relevant opportunities" or anything general. It is about this one role only.
- Use [Name] as a literal placeholder if no recruiter name is given.
- No em dashes. Plain, natural writing, not marketing language. Do not invent employers, numbers or dates.

THE PERSON THEY WOULD BE WRITING TO: __PERSON__
THE JOB:
Company: __COMPANY__
Title: __TITLE__
Location: __LOCATION__
Link: __URL__
Description:
__DESC__

CONTEXT ABOUT CONNOR (resume and his own notes):
__CONTEXT__

CONTACT LINES TO SIGN OFF WITH (skip any that are blank): __CONTACT__"""

RESUME_PROMPT = """Tailor Connor's resume for this specific job. Be concrete and honest: rewrite only what his real experience supports, never invent employers, tools, numbers or dates.

Output plain text in these four sections, no markdown headers deeper than the labels below:

MATCH: two lines on how well his background matches, and the biggest gap.
KEYWORDS TO MIRROR: the exact terms from this posting he should have in the resume, comma separated, marking which ones he already has.
REWRITTEN BULLETS: 5 to 7 bullets he can paste in, each starting with "- ", each mapped to one of his real roles or projects in the form "- [EasierEmails] ...". Lead with the action and the result, use the posting's language where it is truthful.
SUMMARY LINE: one or two sentences for the top of the resume, aimed at this role.

No em dashes anywhere.

THE JOB:
Company: __COMPANY__
Title: __TITLE__
Location: __LOCATION__
Description:
__DESC__

CONNOR'S RESUME AND NOTES:
__CONTEXT__"""


def generate(kind: str, job: dict) -> str:
    s = settings()
    contact = ", ".join(x for x in (s.get("email"), s.get("phone"), s.get("linkedin"), s.get("github")) if x) or "(none)"
    person = "unknown, use [Name]"
    if job.get("contact_name"):
        person = f"{job['contact_name']}, {job.get('contact_title') or 'at ' + job.get('company', '')}"
    desc = (job.get("description") or job.get("why_fit") or "")[:6000]
    repl = {
        "__COMPANY__": job.get("company", ""), "__TITLE__": job.get("title", ""),
        "__LOCATION__": job.get("location", ""), "__URL__": job.get("apply_url") or job.get("url", ""),
        "__DESC__": desc or "(no description saved for this posting)", "__CONTEXT__": _context(),
        "__PERSON__": person, "__CONTACT__": contact,
        "__VOICE__": "in the third person (\"Connor has...\")" if s.get("voice") == "third" else "in the first person (\"I have...\")",
        "__YEARS__": s.get("years", "2 years"),
    }
    template = EMAIL_PROMPT if kind == "email" else RESUME_PROMPT
    for k, v in repl.items():
        template = template.replace(k, str(v))
    text = _chat(template)
    cache = _read(CACHE, {})
    cache.setdefault(job["key"], {})[kind] = {"text": text, "at": time.strftime("%Y-%m-%d %H:%M")}
    _write(CACHE, cache)
    return text


def cached(job_key: str) -> dict:
    return _read(CACHE, {}).get(job_key, {})
