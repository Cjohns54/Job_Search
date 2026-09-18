import json
from unittest import mock

from jobradar.config import load_config, load_companies
from jobradar.filters import CompanyMatcher, RegionClassifier, TitleFilter, min_years_required
from jobradar.models import Job
from jobradar.scoring import assign_priority, heuristic
from jobradar.sources import ats

CFG = load_config()
COS = load_companies()


def test_titles():
    tf = TitleFilter(CFG)
    assert tf.ok("Software Engineer, New Grad")
    assert tf.ok("Member of Technical Staff")
    assert tf.ok("Forward Deployed Engineer")
    assert not tf.ok("Senior Software Engineer")
    assert not tf.ok("iOS Engineer")
    assert not tf.ok("Software Engineer Intern")
    assert not tf.ok("Software Engineer III")


def test_regions():
    rc = RegionClassifier(CFG)
    assert rc.classify("Waterloo, ON") == "gta_kw"
    assert rc.classify("Waterloo, IA") == "us_other"
    assert rc.classify("Cambridge, MA") == "us_other"
    assert rc.classify("Arlington, VA") == "dc_nova"
    assert rc.classify("Reston, Virginia") == "dc_nova"
    assert rc.classify("Seattle, WA") == "us_other"
    assert rc.classify("Remote - Canada") == "remote_ca"
    assert rc.classify("Bengaluru, India") == "non_na"


def test_company_match():
    m = CompanyMatcher(COS)
    assert m.match("Amazon Web Services, Inc.")["company"] == "Amazon"
    assert m.match("Google DeepMind")["company"] == "Google"
    assert m.match("Applebee's") is None
    assert m.match("Synopsys Inc")["group"] == "past_employer"


def test_yoe():
    assert min_years_required("5+ years of experience in backend") == 5
    assert min_years_required("0-2 years experience") is None or min_years_required("0-2 years experience") <= 2
    assert min_years_required("founded 30 years ago") is None


def test_priority():
    j = Job("Cohere", "Software Engineer", "Toronto, ON", "u", "t", region="gta_kw", fit_score=8)
    assert assign_priority(j, CFG) == 1
    j = Job("Stripe", "Software Engineer", "Seattle, WA", "u", "t", region="us_other", fit_score=8,
            watch_company="Stripe", watch_group="unicorn")
    assert assign_priority(j, CFG) == 2
    j.flags = ["clearance"]
    assert assign_priority(j, CFG) == 3
    j = Job("X", "Software Engineer", "Toronto", "u", "t", region="gta_kw", fit_score=1)
    assert assign_priority(j, CFG) == 9


def _fake(payload):
    return mock.patch("jobradar.sources.ats.get_json", return_value=payload)


def test_greenhouse_parse():
    with _fake({"jobs": [{"title": "Software Engineer", "location": {"name": "Toronto, ON"},
                          "absolute_url": "https://x/1", "first_published": "2026-09-15T10:00:00Z",
                          "content": "&lt;p&gt;Build things&lt;/p&gt;"}]}):
        j = list(ats.greenhouse("Stripe", "stripe", CFG))[0]
    assert j.location == "Toronto, ON" and j.posted_at == "2026-09-15" and j.description == "Build things"


def test_lever_parse():
    with _fake([{"text": "Backend Engineer", "categories": {"location": "Washington, DC"},
                 "hostedUrl": "https://x/2", "createdAt": 1789694971000, "descriptionPlain": "Go"}]):
        j = list(ats.lever("Palantir", "palantir", CFG))[0]
    assert j.title == "Backend Engineer" and j.posted_at.startswith("2026")


def test_ashby_parse():
    with _fake({"jobs": [{"title": "AI Engineer", "location": "Toronto", "secondaryLocations": [{"location": "Remote - Canada"}],
                          "jobUrl": "https://x/3", "publishedAt": "2026-09-10T00:00:00Z", "descriptionPlain": "LLMs", "isRemote": False}]}):
        j = list(ats.ashby("Cohere", "cohere", CFG))[0]
    assert "Remote - Canada" in j.location


def test_workday_parse():
    page = {"total": 1, "jobPostings": [{"title": "Software Engineer", "externalPath": "/job/Toronto/SWE_123",
                                         "locationsText": "Toronto, Canada", "postedOn": "Posted 2 Days Ago"}]}
    with mock.patch("jobradar.sources.ats.post_json", return_value=page):
        jobs = list(ats.workday("NVIDIA", "nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite", CFG))
    assert len(jobs) == 1 and jobs[0].url.endswith("/NVIDIAExternalCareerSite/job/Toronto/SWE_123")
    with _fake({"jobPostingInfo": {"jobDescription": "<p>2+ years experience</p>", "location": "Toronto, Canada"}}):
        ats.enrich(jobs[0])
    assert jobs[0].description == "2+ years experience"


def test_heuristic():
    s, why, _ = heuristic(Job("A", "Software Engineer I", "Toronto", "u", "t", description="TypeScript React AWS Python"))
    assert s >= 7 and "typescript" in why


GUEST_HTML = """<section class="core-section-container"><div class="message-the-recruiter">
<h3 class="core-section-container__title">Meet the hiring team</h3>
<div class="base-card relative flex base-main-card flex-wrap">
 <a class="base-card__full-link" href="https://ca.linkedin.com/in/champa-b-12345?trk=public_jobs_job-poster"><span class="sr-only">Champa B</span></a>
 <div class="base-main-card__info"><h3 class="base-main-card__title font-sans">
   Champa B
 </h3><h4 class="base-main-card__subtitle">Senior Sourcing Partner Tech at Hays</h4></div>
</div></div></section>"""


def test_linkedin_contacts():
    from jobradar.sources import linkedin_contacts as lc
    assert lc.job_id("https://www.linkedin.com/jobs/view/4468346526") == "4468346526"
    assert lc.job_id("https://ca.linkedin.com/jobs/view/full-stack-dev-at-hays-4468346526?trk=x") == "4468346526"
    assert lc.job_id("https://boards.greenhouse.io/x/1") is None
    p = lc.parse(GUEST_HTML)
    assert p == [{"name": "Champa B", "title": "Senior Sourcing Partner Tech at Hays",
                  "url": "https://ca.linkedin.com/in/champa-b-12345"}]
    assert lc.parse("<html>no team</html>") == []


def test_store_contacts(tmp_path):
    from jobradar.store import Store
    s = Store(tmp_path / "j.db")
    j = Job("Hays", "Full Stack Developer", "Mississauga, ON", "https://www.linkedin.com/jobs/view/4468346526",
            "jobspy:linkedin", key="k1", priority=1, fit_score=8)
    s.add(j)
    assert [r["key"] for r in s.needs_contact(10)] == ["k1"]
    s.set_contacts("k1", [{"name": "Champa B", "title": "Sourcing", "url": "u"}])
    assert s.needs_contact(10) == [] and s.rows()[0]["contact_name"] == "Champa B"


EXPORT = """Notes:
"When exporting your connection data, you may notice that some of the email addresses are missing."

First Name,Last Name,URL,Email Address,Company,Position,Connected On
Sarah,Kim,https://www.linkedin.com/in/sarahkim,,Amazon Web Services (AWS),Software Development Engineer II,03 Mar 2024
Jordan,Patel,https://www.linkedin.com/in/jpatel,,Hays Specialist Recruitment,Recruitment Consultant,11 Jan 2025
Alex,Ng,https://www.linkedin.com/in/alexng,,Metalab,Designer,01 Feb 2023
Sam,Lee,https://www.linkedin.com/in/samlee,,Self Employed,Founder,01 Feb 2023
"""


def test_connections_parse_and_match(tmp_path):
    import io, zipfile
    from jobradar import connections as c
    rows = c.parse_export(EXPORT.encode())
    assert len(rows) == 4 and rows[0]["name"] == "Sarah Kim" and rows[0]["company"].startswith("Amazon")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Basic_LinkedInDataExport/Connections.csv", EXPORT)
    assert len(c.parse_export(buf.getvalue())) == 4
    m = c.Matcher(rows)
    assert [p["name"] for p in m.at("Amazon")] == ["Sarah Kim"]           # watch alias: AWS -> Amazon
    assert [p["name"] for p in m.at("Hays")] == ["Jordan Patel"]          # prefix match
    assert m.at("Meta") == []                                             # Metalab is not Meta
    assert m.at("Self Employed") == []


def test_connections_rejects_other_files():
    import pytest
    from jobradar import connections as c
    with pytest.raises(ValueError):
        c.parse_export(b"just,some,csv\n1,2,3")
