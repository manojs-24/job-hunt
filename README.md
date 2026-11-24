# Job-Search

A personal job-hunting assistant. It searches three job APIs at once, reads company careers pages directly,
remembers every job it has found, scores each new job with Claude for how well it fits you, and can read your
**resume** to work out which roles to search for. Everything runs on your own computer, with a Streamlit web
interface and a local SQLite database.

## What it does

- **Search jobs:** enter a role, your experience and a city. Jobs come from JSearch (RapidAPI),
  SerpApi (Google Jobs) and OpenWeb Ninja, are merged, and saved.
- **Never repeats:** a job already in the database is not saved or scored again.
- **Fit score:** Claude gives each new job a 0-100 score, the experience it asks for, and a short reason.
- **Match my resume:** upload a PDF, Word file or text. Claude lists your skills and the roles you could apply
  for. You review and approve them, then each role is searched and every job shows the skills you match
  and the skills you lack.
- **Startups & remote:** reads the careers pages of companies on your watchlist (Greenhouse, Lever, Ashby,
  SmartRecruiters and Workable, the hiring tools many startups use) plus the free Remotive and Remote OK boards.
  It costs no job-API credits. You choose a location (a city, several cities, a state, "NCR" or all of India),
  and can also keep remote jobs. Only jobs whose title fits your roles are saved.
- **Find companies:** type a place and see which **companies** are hiring there, each with a link to its own
  careers page so you can apply directly. Names come from your watchlist, your saved jobs, Y Combinator's public
  startup list (many small, little-known startups) and Claude's suggestions. Each one is checked against its real
  careers page. Free: no job-API credits are used.
- **Track:** tick **Save** to bookmark a job and **Applied** once you have applied. Each job also shows which
  API or careers page found it.
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
| **Startups & remote** | **Find jobs** tab: enter roles, a location (or start from a saved resume profile), pick company types, press **Find jobs**. **Company list** tab: tick which companies to check, change their type, remove some, or add your own by pasting the name from their careers-page address. |
| **Find companies** | Type a location (and optionally roles), choose which name sources to use, and press **Find companies**. Companies with jobs open there are listed with a **Careers page** link. A second table lists companies that may hire there but whose careers page can't be read automatically, each with a website or search link. |
| **All jobs** | Every job in the database. Filter by text, saved, applied, API, job board, minimum score; sort; download. |

Notes on **Match my resume**:

- Nothing is searched until you press **Approve and search**. The page shows how many API requests the run
  will use first.
- A run searches at most **5 roles** to protect your free API allowance. If Claude suggests more, the best 5
  are selected and the rest stay in the list.
- Tick "Save this profile" to reuse it later with **Use**, without uploading again.
- Scanned PDFs (images of text) cannot be read. Paste the text instead.

Notes on **Find companies**:

- **Verified** means the company's careers page was read and has jobs open in your place right now. **Not
  verified** means it may well hire there, but its careers page is not one the app can read, so there is no live
  count. Nothing is listed as hiring on Claude's word alone.
- **Order by:** "Most active hiring" ranks by jobs that match your roles, then recent postings, then number of
  openings. "Smallest teams first" is a rough guide to lesser-known companies. It only works where a team size is
  known, which is mostly Y Combinator startups, and it is not a quality rating.
- A big place can take several runs. Each run checks up to 100 new company names and remembers what it learned,
  so press **Find companies** again to continue.
- Untick **Ask Claude for more names** to run the page with no Claude call at all.
- Salary, benefits and company ratings are not shown. No free source has that data for these companies.

## Settings (`.env`)

| Key | Purpose |
| --- | --- |
| `JSEARCH_RAPIDAPI_KEY`, `SERPAPI_KEY`, `OPENWEB_NINJA_API_KEY` | Job sources for **Search jobs** and **Match my resume**. A missing key is skipped and shown as "skipped" on the page. |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL` | Your Claude key and proxy URL (used as the SDK `base_url`). Without a key, jobs are still saved but unscored, and resume analysis and company suggestions are unavailable. |
| `CLAUDE_MODEL` | Defaults to `claude-opus-5-5`. A cheaper model such as `claude-sonnet-5-5` or `claude-haiku-5-5` also works. Your proxy must serve the name you set. |
| `COUNTRY` | Country code for searches. Default `in` (India). |
| `OPENWEB_NINJA_URL`, `OPENWEB_NINJA_AUTH_HEADER` | Default to the Real-Time Jobs Data Google Jobs endpoint and the `X-API-Key` header. |
| `OPENWEB_NINJA_PAGES` | Pages per search (10 jobs, 1 request each). Default 1, because the free plan is a hard 100 requests a month. |

**Startups & remote** and **Find companies** need no key of their own. They read public web addresses.

`.env` takes priority over variables already set in your shell. The sidebar ticks show which keys are
present, not whether they work. The coloured badges after a search show what each API actually returned,
including errors such as "not subscribed".

**Free-plan limits:** JSearch is 200 requests a month (a search uses up to 2) and OpenWeb Ninja is 100 (a search
uses 1). A resume search uses about 5 requests per role.

## How it works

The app has three ways of getting jobs, and one place they all end up: `jobs.db`.

```
 Browser (Streamlit pages: Dashboard, Search jobs, Match my resume, Startups & remote, Find companies, All jobs)
        |
        v
 A. SEARCH APIS  (Search jobs, Match my resume)      app/service.py
        |  1. fetch        app/providers/  ->  JSearch + SerpApi + OpenWeb Ninja (run in parallel)
        |  2. clean        app/dedupe.py   ->  drop incomplete/old jobs, merge the same job from two APIs
        |  3. remember     app/db.py       ->  SQLite: insert only jobs not seen before
        |  4. score        app/scoring.py  ->  Claude scores NEW jobs only (via app/llm.py)
        |  5. save scores  app/db.py
        |
 B. CAREERS PAGES  (Startups & remote)               app/service.py
        |  1. read         providers/careers.py + remote_boards.py -> every open job of each watchlist company
        |  2. place        providers/geo.py       ->  keep jobs in your location (and remote, if ticked)
        |  3. title        app/relevance.py       ->  keep titles that fit your roles (free, no Claude)
        |  4. save + score the best new ones, up to a per-run limit, then the same steps 3-5 as above
        |
 C. FIND COMPANIES  (Find companies)                 app/company_finder.py
        |  gather names -> look up each careers page -> count jobs in your place -> rank   (no jobs are saved)
        v
   jobs.db  <-- read by every page (tables, filters, dashboard, downloads)

 Resume path:  app/resume.py  reads PDF/Word/text -> Claude -> skills + roles
               -> you review and approve -> service.run_resume_search -> same steps 1-5, scored against your skills
```

### Where Claude is used (and where it is not)

| Task | Done by |
| --- | --- |
| Scoring each new job, and extracting the experience it asks for | Claude |
| Reading your resume into skills and roles | Claude |
| Suggesting company names for a place (optional) | Claude |
| Reading careers pages, counting jobs, matching a page to a company, filtering by place and title, ranking | Plain code, no AI |

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

### Careers pages: how a company's jobs are read

Many companies publish their open jobs as plain public data on a hiring tool. The app reads these five, with no
key and no job-API credits (the address patterns are in `app/providers/careers.py`):

| Tool | Address pattern |
| --- | --- |
| Greenhouse | `boards-api.greenhouse.io/v1/boards/<name>/jobs` |
| Lever | `api.lever.co/v0/postings/<name>` |
| Ashby | `api.ashbyhq.com/posting-api/job-board/<name>` |
| SmartRecruiters | `api.smartrecruiters.com/v1/companies/<name>/postings` |
| Workable | `apply.workable.com/api/v1/widget/accounts/<name>` |

The reply is live, so it shows today's openings, and nothing from it is stored except the jobs that are saved.
You can open one of these addresses in a browser to see the raw list. Remotive and Remote OK are read the same
way (`app/providers/remote_boards.py`), and both ask to be credited, so each job keeps its original link and its
Source column names the board.

### Find companies: how a company's careers page is found

The app does not search the web for careers pages. It works out each company's page from its name:

1. **From a link you already have.** A saved job link like `jobs.lever.co/meesho/abc` names the tool and the
   company outright. This is the most reliable route and is tried first.
2. **From your watchlist,** where the tool and name are already known.
3. **By guessing.** The company name becomes likely names (`oddnamelabs`, `odd-name-labs`), and for a Y Combinator
   startup the first part of its website too. Each guess is tried on Greenhouse, Lever, Ashby and SmartRecruiters.
4. **A guess is kept only if the page belongs to that company.** The page's stated company name, or the company
   name in its job texts, must match. A board called `blue` is not accepted for "Blue Yonder". The check is strict
   on purpose, so a correct page is sometimes missed and the company shows as not verified.
5. **The answer is remembered** in the `board_lookups` table, so later runs skip the guessing.

**Workable is not used for guessing.** It blocks after about a dozen requests (HTTP 429), even at one a second.
It is still used whenever the name is already known.

Companies that run their own careers site, or a tool the app cannot read, cannot be verified. They appear in the
"not verified" table with a link.

### Y Combinator startup list

Y Combinator is a startup funder. A volunteer-run copy of its public company list (about 6,400 startups, with city,
team size, batch, website and a "hiring" flag) is used as a source of company names, which helps find small
startups you would not think to type in. It is free and needs no key.

- It is downloaded once (about 11 MB) and a trimmed copy is saved as **`.yc_cache.json`** in the project folder.
  The app reuses that file for **7 days**, then downloads a fresh one. If a download fails it uses the old file.
- It is not YC's own service, so it could change or disappear. It covers only YC-backed companies.
- YC's "hiring" flag is theirs and can be out of date. The page uses it only to order the "not verified" list.
- Delete `.yc_cache.json` any time to force a fresh download. It is safe to delete.

### What is stored, and what is not

| What | Where | Lifetime |
| --- | --- | --- |
| Jobs you saved, scores, saved/applied flags | `jobs.db`, `jobs` table | Until you delete them |
| Your company watchlist | `jobs.db`, `companies` table | Until you edit it. Seeded once with a starter list that you should edit |
| Which careers page each company uses | `jobs.db`, `board_lookups` table | Re-checked if the page stops working |
| Saved resume profiles | `jobs.db`, `profiles` table | Until you delete them |
| The Y Combinator list | `.yc_cache.json` | 7 days |
| **Find companies results** (the "verified" and "not verified" tables) | Nowhere | Shown on the page only, rebuilt on every run |
| **Job openings on careers pages** | Nowhere | Read live on every run |

### Project layout

```
Job-Search/
  streamlit_app.py      entry point: page navigation and the sidebar key status
  start.bat             Windows launcher
  pyproject.toml        dependencies (managed by uv)
  .env / .env.example   your keys and settings
  jobs.db               the SQLite database (created on first run)
  .yc_cache.json        saved copy of the Y Combinator list (created on first use, safe to delete)
  .streamlit/config.toml  dark theme and server settings
  views/                the six pages
    dashboard.py  search.py  resume_match.py  startups.py  companies.py  all_jobs.py
  app/
    service.py          the search pipelines (single search, resume search, startups & remote)
    company_finder.py   the Find companies pipeline
    finder.py           locate a company's careers page; Claude's company-name suggestions
    yc.py               download, cache and filter the Y Combinator list
    relevance.py        free title matching and ranking
    seed_companies.py   the starter watchlist (a guess to edit)
    providers/
      jsearch.py  serpapi.py  openwebninja.py   the three search APIs
      careers.py        read careers pages on the five hiring tools
      remote_boards.py  Remotive and Remote OK
      geo.py            "is this job in the place I typed?" (city spellings, NCR, remote)
      base.py           shared helpers
    dedupe.py           company/title cleaning, fingerprints, merging, age filter
    db.py               tables, "insert only if new", filters, watchlist, saved profiles, migrations
    llm.py              the single place that asks Claude for structured JSON
    scoring.py          job scoring prompts and parsing
    resume.py           read PDF/Word/text, analyse with Claude, clean the profile
    dashboard.py        the numbers behind the dashboard
    charts.py           Altair charts (one blue series, light and dark)
    ui.py               the shared jobs table with Save/Applied checkboxes
    export.py           CSV and Excel files
    config.py           loads .env
  tests/                191 tests; Claude, the job APIs and the careers pages are faked
```

### The database

`jobs.db` has these tables:

| Table | Holds |
| --- | --- |
| `jobs` | every saved job: score, skills, saved/applied flags, which APIs or careers pages found it, company type |
| `searches` | a log of each search |
| `profiles` | saved resume profiles |
| `companies` | your watchlist: name, hiring tool, name on that tool, type, ticked or not, last check |
| `board_lookups` | which careers page each company was found to use (or that none was found) |
| `meta` | small settings, such as whether the starter watchlist was already added |

Older databases are upgraded automatically when the app starts.

**Privacy:** `jobs.db` holds your saved jobs and resume profile, and `.env` holds your keys. Keep both out of any
public repository. The sample `.gitignore` excludes `.env`, `.yc_cache.json`, `.venv` and caches. Check that it
also lists `jobs.db` before you push.

## Limits to know about

- **Only five careers-page tools can be read.** Companies on their own site, or on other tools, show as "not
  verified". In a live test, about 6 of 34 Bengaluru Y Combinator startups marked as hiring could be read.
- **Only Y Combinator-backed startups come from that list.** A good startup outside it appears only if it is in
  your saved jobs, your watchlist, or Claude's suggestions.
- **Company discovery is limited by its sources.** No free source rates companies or lists pay and benefits.
- **Careers-page jobs without a location** are counted as possible matches when remote jobs are included.
- **Title matching is simple.** A role mismatch can slip through, and seniority only changes the ranking.
- **Remote boards are thin:** the free feeds expose only the latest few dozen to hundred jobs.

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
| Find companies: "companies were left unchecked" | The run checks up to 100 new names. Press **Find companies** again to continue. |
| Find companies: "Could not load the Y Combinator list" | The download failed and there is no saved copy. Check your connection and retry. The page still works without it. |
| A company shows "not verified" | Its careers page is on a site or tool the app cannot read. Use the website or search link. |
| A careers page shows "rate limited" | The site refused too many requests. Wait a few minutes and run again. |
| Changed `.env` or the theme but nothing changed | Restart the app. Streamlit reads both only at start-up. |
