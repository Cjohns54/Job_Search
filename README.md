# Job Radar

Runs every night on GitHub Actions and gives you a morning list of new jobs that fit your resume.

Two sweeps feed one deduped table:

- **Company watch** (`companies.csv`): every relevant posting at your target companies and past employers, anywhere in North America. Uses public ATS APIs (Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Amazon). Companies with custom career sites are searched by name on LinkedIn/Indeed.
- **Regional sweep**: any company, but only in Toronto/Waterloo, DC/Northern Virginia, or remote Canada. Uses LinkedIn, Indeed, Glassdoor and Google Jobs (via python-jobspy) plus the SimplifyJobs new-grad list.

Then: title filter → skip anything seen before → fetch descriptions → location + years-of-experience filter → fit score (LLM or keywords) → priority:

| Priority | Meaning |
|---|---|
| P1 | Near you (or remote Canada), strong fit. Past employers near you land here too |
| P2 | Watch company elsewhere in North America, strong fit |
| P3 | Near you, partial fit |
| P4 | Passed filters, weaker fit |

Clearance / US-citizen-only postings are flagged and bumped down one level, not removed.

## Daily use

```bash
cd ~/Desktop/Job_Search/job-radar && python3 -m jobradar app
```

Pulls the latest jobs, opens a tracker in your browser. Every job has **Applied** and **Ineligible** toggles; filter by To do / Applied / Not applied / Ineligible, priority, region, date found, or search. Toggles save instantly to `data/status.json`. Press Ctrl+C in Terminal when done and your statuses get committed and pushed as a backup.

## Outputs

- `output/latest.html`: the morning page (new today, grouped by priority, with apply links)
- `output/jobs.csv`: everything ever shown
- Google Sheet (optional): new rows appended nightly with a blank `status` column for you to track applications
- `data/jobs.db`: SQLite memory of every job already seen, so nothing repeats

## Setup (about 15 minutes)

1. Create a **private** GitHub repo and push this folder.
2. Repo → Settings → Secrets and variables → Actions:
   - Secret `RESUME_TEXT`: paste the contents of `resume.txt` (it's gitignored so your phone number isn't committed)
   - Secret `LLM_API_KEY`: OpenAI key (or Groq key)
   - Optional variables `LLM_BASE_URL` (e.g. `https://api.groq.com/openai/v1`) and `LLM_MODEL`
3. Actions tab → "Job Radar (nightly)" → Run workflow with **detect_ats** checked. It checks every company's job board. Update `ats`/`ats_slug` in `companies.csv` for any that show NOT FOUND or have a better hit.
4. Run workflow again normally. The first run is big (everything counts as new). After that it's only new postings.

### Google Sheet (optional)

1. Google Cloud console → create a service account → create a JSON key.
2. Enable the Google Sheets API for that project.
3. Make a sheet, share it with the service account's email (Editor).
4. Secrets: `GOOGLE_SERVICE_ACCOUNT_JSON` (whole JSON file) and `SHEET_ID` (the long id in the sheet URL).

## Running locally

```bash
pip install -r requirements.txt
python -m jobradar run --no-llm              # keyword scoring, all sources
python -m jobradar run --only github,ats     # subset of sources
python -m jobradar detect-ats                # verify job-board slugs
pytest
```

## Tuning

Everything is in `config.yaml`: title include/exclude words, max years of experience, regions and cities, search terms, hubs and radius, score thresholds. Add companies to `companies.csv` (the `match_names` column is `|`-separated aliases used to recognize the company in LinkedIn/Simplify results).

## Known limits

- LinkedIn and Glassdoor sometimes rate-limit GitHub's servers. A failed search is skipped, never fatal. Indeed and Simplify are the most reliable.
- Simplify and LinkedIn results often have no description, so those get scored on title + company only.
- Workday and Amazon endpoints are undocumented and can change. Check the run log if a company suddenly returns 0.
