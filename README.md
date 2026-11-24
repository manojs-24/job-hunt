# Job-Search

A personal job-hunting assistant. It searches three job APIs at once, remembers every job it has
found, scores each new job with Claude for how well it fits you, and can read your **resume** to work out
which roles to search for. Everything runs on your own computer, with a Streamlit web interface and a
local SQLite database.

## What it does

- **Search jobs:** enter a role, your experience and a city. Jobs come from JSearch (RapidAPI),
  SerpApi (Google Jobs) and OpenWeb Ninja, are merged, and saved.
- **Never repeats:** a job already in the database is not saved or scored again.
- **Fit score:** Claude gives each new job a 0-100 score, the experience it asks for, and a short reason.
- **Match my resume:** upload a PDF, Word file or text. Claude lists your skills and the roles you could apply
  for. You review and approve them, then each role is searched and every job shows the skills you match
  and the skills you lack.
- **Track:** tick **Save** to bookmark a job and **Applied** once you have applied. Each job also shows which
  API found it.
- **Dashboard:** charts of jobs found, fit scores, experience asked, sources, and the best matches you have
  not applied to yet.
- **Download:** CSV or Excel of any list you are looking at.

## How to run

You need [uv](https://docs.astral.sh/uv/getting-started/installation/). It installs Python 3.10+ and every
dependency for you, so there is nothing else to set up.

**1. Add your keys.** Copy the template and fill it in (see [Settings](#settings-env)):

```bash
copy .env.example .env        # Windows (macOS/Linux: cp .env.example .env)
```

**2. Start the app.** Either double-click **`start.bat`** (Windows), or run:

```bash
uv run streamlit run streamlit_app.py
```

The first run creates `.venv` and installs the packages, which takes a minute. Then open
**http://localhost:8501**. Press `Ctrl+C` in the terminal to stop the app.

`start.bat` also creates `.env` from the template and opens it in Notepad if it does not exist yet.

**3. Run the tests** (they never call a real API or spend credits):

```bash
uv run pytest
```

## Using the app

| Page | What to do there |
| --- | --- |
| **Dashboard** | See totals and charts. Filter by time range (7 days, 30 days, all) and by the role searched. Tick **Applied** in the "best matches" table. |
| **Search jobs** | Enter role, experience, location and optionally "posted within N days", then press **Search jobs**. Takes up to a minute. |
| **Match my resume** | 1) Upload or paste your resume and press **Analyse**. 2) Check the skills and roles, edit them, pick up to 5 roles. 3) Press **Approve and search**. |
| **All jobs** | Every job in the database. Filter by text, saved, applied, API, job board, minimum score; sort; download. |

Notes on **Match my resume**:

- Nothing is searched until you press **Approve and search**. The page shows how many API requests the run
  will use first.
- A run searches at most **5 roles** to protect your free API allowance. If Claude suggests more, the best 5
  are selected and the rest stay in the list.
- Tick "Save this profile" to reuse it later with **Use**, without uploading again.
- Scanned PDFs (images of text) cannot be read. Paste the text instead.

## Settings (`.env`)

| Key | Purpose |
| --- | --- |
| `JSEARCH_RAPIDAPI_KEY`, `SERPAPI_KEY`, `OPENWEB_NINJA_API_KEY` | Job sources. A missing key is skipped and shown as "skipped" on the page. |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL` | Your Claude key and proxy URL (used as the SDK `base_url`). Without a key, jobs are still saved but unscored, and resume analysis is unavailable. |
| `CLAUDE_MODEL` | Defaults to `claude-opus-5-5`. A cheaper model such as `claude-sonnet-5-5` or `claude-haiku-5-5` also works. Your proxy must serve the name you set. |
| `COUNTRY` | Country code for searches. Default `in` (India). |
| `OPENWEB_NINJA_URL`, `OPENWEB_NINJA_AUTH_HEADER` | Default to the Real-Time Jobs Data Google Jobs endpoint and the `X-API-Key` header. |
| `OPENWEB_NINJA_PAGES` | Pages per search (10 jobs, 1 request each). Default 1, because the free plan is a hard 100 requests a month. |

`.env` takes priority over variables already set in your shell. The sidebar ticks show which keys are
present, not whether they work. The coloured badges after a search show what each API actually returned,
including errors such as "not subscribed".

**Free-plan limits:** JSearch is 200 requests a month (a search uses up to 2) and OpenWeb Ninja is 100 (a search
uses 1). A resume search uses about 5 requests per role.

## How it works

```
 Browser (Streamlit pages: Dashboard, Search jobs, Match my resume, All jobs)
        |
        v
   app/service.py  ----  the search pipeline
        |
        |  1. fetch        app/providers/  ->  JSearch + SerpApi + OpenWeb Ninja (run in parallel)
        |  2. clean        app/dedupe.py   ->  drop incomplete/old jobs, merge the same job from two APIs
        |  3. remember     app/db.py       ->  SQLite: insert only jobs not seen before
        |  4. score        app/scoring.py  ->  Claude scores NEW jobs only (via app/llm.py)
        |  5. save scores  app/db.py
        v
   jobs.db  <-- read by every page (tables, filters, dashboard, downloads)

 Resume path:  app/resume.py  reads PDF/Word/text -> Claude -> skills + roles
               -> you review and approve -> service.run_resume_search -> same steps 1-5, scored against your skills
```

### Why jobs never repeat

Each job gets a fingerprint: the company name (with Pvt/Ltd/Technologies removed), the sorted words of the
title, and the city. It is also matched on its apply link, and by similar title within the same company. A job
already in `jobs.db` only has its "last seen" time and the list of APIs that found it updated. It is not
inserted again and never sent to Claude again, so repeat searches cost no extra Claude usage.

### How scoring works

New jobs go to Claude in batches of 10, with only the first 1,500 characters of each description. Claude
returns a 0-100 score, the years of experience the job asks for, and a short reason. On the resume page it
also sees your skills and returns the ones you match and the ones you lack. If Claude is not set up or fails,
jobs are still saved and shown, just without a score.

### Project layout

```
Job-Search/
  streamlit_app.py      entry point: page navigation and the sidebar key status
  start.bat             Windows launcher
  pyproject.toml        dependencies (managed by uv)
  .env / .env.example   your keys and settings
  jobs.db               the SQLite database (created on first run, git-ignored)
  .streamlit/config.toml  dark theme and server settings
  views/                the four pages
    dashboard.py  search.py  resume_match.py  all_jobs.py
  app/
    service.py          the search pipeline (single search and resume search)
    providers/          one module per job API, plus shared helpers
    dedupe.py           company/title cleaning, fingerprints, merging, age filter
    db.py               tables, "insert only if new", filters, saved profiles, migrations
    llm.py              the single place that asks Claude for structured JSON
    scoring.py          job scoring prompts and parsing
    resume.py           read PDF/Word/text, analyse with Claude, clean the profile
    dashboard.py        the numbers behind the dashboard
    charts.py           Altair charts (one blue series, light and dark)
    ui.py               the shared jobs table with Save/Applied checkboxes
    export.py           CSV and Excel files
    config.py           loads .env
  tests/                63 tests; Claude and the job APIs are faked
```

### The database

`jobs.db` has three tables: `jobs` (every job, its score, skills, saved/applied flags and which APIs found it),
`searches` (a log of each search) and `profiles` (saved resume profiles). Older databases are upgraded
automatically when the app starts.

## Troubleshooting

| You see | Meaning and fix |
| --- | --- |
| `jsearch: HTTP 403 "You are not subscribed to this API"` | Your RapidAPI key is not subscribed to the JSearch API. Subscribe on rapidapi.com, or leave the key blank. |
| `openwebninja: HTTP 403` | The key has no plan for the Real-Time Jobs Data API. Check your OpenWeb Ninja plan. |
| `quota exhausted or rate limited (HTTP 429)` | You used up that API's monthly allowance, or searched too fast. Wait or use another source. |
| "Scoring skipped: ANTHROPIC_API_KEY is not set" | Add the key to `.env` and restart. Jobs still save without scores. |
| "Scoring failed ... model not found" | Your proxy does not serve `CLAUDE_MODEL`. Set a model name it supports. |
| "Resume analysis needs Claude" | Set `ANTHROPIC_API_KEY` (and `ANTHROPIC_BASE_URL` if you use a proxy) in `.env` and restart. |
| "Not enough text found" on a PDF | It is probably a scan. Paste the resume text instead. |
| Changed `.env` or the theme but nothing changed | Restart the app. Streamlit reads both only at start-up. |
