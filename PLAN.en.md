# Job Market Monitor — Execution Plan (PLAN.en.md)

> This document is the project's single source of truth, written for both humans and AI
> collaborators. Any AI session should read this document in full before making changes.
> If the implementation conflicts with this plan, the plan wins; if the plan itself needs
> changing, update this document first, then the code.
> Status markers: when a Phase is completed, change its `status: todo` to `status: done`
> and add a one-line note.
> A Chinese version exists at `PLAN.md`. Keep the two in sync when editing either.

---

## 1. Project Overview

**Goal**: a job market monitoring system that continuously ingests tech job postings from
three regions (Singapore, US, Australia), classifies them into a custom taxonomy with an
LLM, and provides:

1. **Job layer**: postings filtered to the user's target directions, pushed via Telegram
   as instant alerts + digests
2. **Market layer**: a dashboard for posting-volume trends, cross-region comparison,
   skill-demand evolution, and emerging-title detection

**Nature**: a personal project, built to portfolio standard — clean architecture, README,
architecture diagram, an LLM-classification eval, reproducible deployment.

**User background**: currently a Data Analyst, considering a transition toward DS/AI roles.

**Core design principles**:
- Three decoupled layers: pipeline → DB → frontend. The frontend (Streamlit) can later be
  replaced with Next.js without touching the rest
- **Start accumulating data as early as possible**: trend analysis depends on time series,
  so ingest going live is the top priority; the dashboard can come later
- Use only legal, stable data sources (public APIs); do not scrape sites whose ToS forbid
  it (LinkedIn/Indeed/Seek web pages)
- Everything runs on free tiers; the only recurring cost is the LLM API (target < $15/month)

## 2. Settled Decisions

| Decision | Outcome |
|---|---|
| Positioning | Job hunting + market analysis, equal weight |
| Data sources | ATS public APIs primary (Greenhouse/Lever/Ashby) + Adzuna for market breadth |
| Classification | LLM (claude-haiku-4-5) structured extraction, with a human-labeled eval set |
| Dashboard | Streamlit (first); decoupled architecture, swappable for Next.js later |
| Alerts | Telegram bot (instant pushes + daily digest) |
| Storage | SQLite, DB file committed to the repo (see §5); migrate to Supabase if it outgrows this |
| Scheduling | GitHub Actions cron |
| Language | Python 3.11+ |

## 3. Role Taxonomy

Classification output must be one of the slugs below. **Tier determines usage**:
tier 1 = target roles (alert-eligible, shown in the job layer); tier 2 = market signal
(market-layer stats only, never alerted); tier 3 = everything else.

| slug | Tier | Definition / decision hints |
|---|---|---|
| `data_scientist` | 1 | DS, Applied Scientist, Decision Scientist, etc. |
| `ai_engineer` | 1 | AI Engineer, LLM Engineer, GenAI Engineer; application-layer LLM development |
| `ml_engineer` | 1 | MLE, ML Platform, ML Infra; includes engineering-leaning Research Engineer |
| `agentic_engineer` | 1 | Agent Engineer, Agentic Engineer, AI agent development; emerging title, track closely |
| `forward_deployed_engineer` | 1 | FDE, Forward Deployed SWE; includes clearly customer-embedded Solutions Engineer/Architect |
| `engineering_analyst` | 1 | Engineering Analyst, Analytics Engineer, and technically-leaning Product/Business Analyst |
| `data_analyst` | 1 | The user's current role. Both a fallback option and a basis for comparing DA vs DS/MLE supply/demand to inform the transition |
| `software_developer` | 2 | Generic SWE (frontend/backend/full-stack/mobile/infra). **Not a target role** — serves only as the industry baseline: a reference for overall tech-hiring temperature |
| `other` | 3 | None of the above (PM, design, sales, ops, etc.) |

Boundary rules (to be written into the classification prompt):
- Ambiguous titles like "Member of Technical Staff" → decide from the job description,
  not the title
- Research Scientist (publication-oriented) → `other`; Research Engineer
  (engineering-oriented) → `ml_engineer`
- When multiple categories fit, pick the most specific:
  `agentic_engineer` > `ai_engineer` > `ml_engineer` > `software_developer`
- Solutions Engineer defaults to `other`, unless the JD clearly describes
  forward-deployed / on-site / customer-embedded delivery → `forward_deployed_engineer`

## 4. System Architecture

```
GitHub Actions cron (1-2 times daily, UTC, off-peak)
  └─ python -m jobmon.run_pipeline
       1. ingest    : each source adapter fetches → normalized schema
       2. upsert    : write to SQLite; update first_seen/last_seen/is_active
       3. classify  : call LLM for new, unclassified jobs → classifications table
       4. alert     : rules engine match → Telegram instant push + digest
       5. commit    : commit the updated data/jobs.db back to the repo

Streamlit Cloud (deployed separately, read-only view of jobs.db from the repo)
  └─ dashboard/app.py : job-layer + market-layer page groups
```

Every step is idempotent: re-running the pipeline must not produce duplicate rows,
duplicate alerts, or duplicate billable LLM calls.

## 5. Data Model (SQLite)

DB file: `data/jobs.db`, committed to the repo (single file, naturally versioned,
directly readable by Streamlit Cloud). Size control: `description` truncated to 8000
chars; expected growth is tens of MB per year, acceptable. If it exceeds ~200MB or
concurrent writes become necessary, migrate to Supabase Postgres (schema-compatible,
switch at the SQLAlchemy layer).

```sql
CREATE TABLE jobs (
  id             INTEGER PRIMARY KEY,
  source         TEXT NOT NULL,       -- greenhouse | lever | ashby | adzuna
  source_job_id  TEXT NOT NULL,       -- unique ID within the source
  company        TEXT NOT NULL,
  title          TEXT NOT NULL,
  location_raw   TEXT,
  country        TEXT,                -- SG | US | AU | OTHER (rule-parsed in the adapter)
  is_remote      INTEGER DEFAULT 0,
  url            TEXT,
  description    TEXT,                -- plain text, ≤8000 chars
  posted_at      TEXT,                -- source-provided publish time, ISO 8601, nullable
  first_seen     TEXT NOT NULL,       -- first time this system saw it
  last_seen      TEXT NOT NULL,       -- most recent time this system saw it
  is_active      INTEGER NOT NULL DEFAULT 1,
  closed_at      TEXT,                -- set after missing from 2 consecutive runs
  dedupe_key     TEXT,                -- hash of norm(company)+norm(title)+country
  UNIQUE(source, source_job_id)
);

CREATE TABLE classifications (
  job_id           INTEGER PRIMARY KEY REFERENCES jobs(id),
  category         TEXT NOT NULL,     -- taxonomy slug
  tier             INTEGER NOT NULL,
  seniority        TEXT,              -- intern|junior|mid|senior|staff_plus|manager|unknown
  skills           TEXT,              -- JSON array, normalized lowercase, e.g. ["python","langgraph","mcp"]
  salary_min       REAL,
  salary_max       REAL,
  salary_currency  TEXT,
  visa_sponsorship TEXT,              -- yes|no|unknown
  confidence       REAL,              -- 0-1, model self-reported
  model            TEXT NOT NULL,
  classified_at    TEXT NOT NULL
);

CREATE TABLE alerts_sent (             -- alert idempotency: one send per job+rule
  job_id    INTEGER REFERENCES jobs(id),
  rule_name TEXT,
  sent_at   TEXT NOT NULL,
  PRIMARY KEY (job_id, rule_name)
);

CREATE TABLE ingest_runs (             -- run log, for monitoring pipeline health itself
  run_at     TEXT NOT NULL,
  source     TEXT NOT NULL,
  ok         INTEGER NOT NULL,
  jobs_seen  INTEGER,
  jobs_new   INTEGER,
  error      TEXT
);
```

**Lifecycle rules**:
- On upsert hitting `(source, source_job_id)` → update `last_seen`, set `is_active=1`
- If a source's run succeeded but a job didn't appear, and it has been absent for 2
  consecutive runs → `is_active=0`, set `closed_at`
  (never mark jobs closed when the source itself failed to fetch — avoids false kills)
- `closed_at - first_seen` ≈ time-to-fill, a market-layer metric

**Cross-source dedupe**:
`dedupe_key = sha1(normalize(company) + "|" + normalize(title) + "|" + country)`.
ATS data is authoritative; when an Adzuna job hits an existing dedupe_key, only update
last_seen instead of creating a new row. normalize: lowercase, strip punctuation, strip
suffixes like "inc/pte/ltd", collapse whitespace. v1 uses exact fingerprints; fuzzy
matching stays in the backlog.

## 6. Data Source Specs

### 6.1 Greenhouse (no auth)
- `GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true`
- Field mapping: `id→source_job_id`, `title`, `location.name→location_raw`,
  `absolute_url→url`, `content→description` (HTML, convert to plain text),
  `updated_at→posted_at` (approximation)

### 6.2 Lever (no auth)
- `GET https://api.lever.co/v0/postings/{site}?mode=json`
- `id`, `text→title`, `categories.location→location_raw`, `hostedUrl→url`,
  `descriptionPlain→description`, `createdAt→posted_at` (epoch ms)

### 6.3 Ashby (no auth)
- `GET https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=true`
- `jobs[]`: `id`, `title`, `location`, `jobUrl→url`, `descriptionHtml→description`;
  if compensation fields are present, store them directly (saves some LLM extraction)
- Only take jobs with `isListed: true`

### 6.4 Adzuna (requires app_id + app_key, free signup)
- `GET https://api.adzuna.com/v1/api/jobs/{country}/search/{page}`, country ∈ {us, au, sg}
- Params: rotate `what` through taxonomy keywords (e.g. "data scientist",
  "machine learning engineer"), `results_per_page=50`, `max_days_old=2` (incremental only)
- Free quota is limited (confirm the actual quota after signup). The adapter must:
  rate-limit, back off on failure, and skip + log when the daily quota is exhausted
- Store Adzuna's `category`/`salary_min`/`salary_max` fields directly
- Note: Adzuna descriptions are truncated snippets; classification quality will be lower
  than ATS full text and confidence will skew low — this is expected

### 6.5 Company list `config/companies.yaml`
```yaml
# entry format
- name: Anthropic
  ats: greenhouse        # greenhouse | lever | ashby
  token: anthropic       # board_token / site / org
  note: ai-lab
```
- Phase 1 generates a seed list of **150-300 companies**, composed of: AI labs and AI
  infra (Anthropic/OpenAI/Mistral/HuggingFace/Scale/…), mid-to-large US tech, Singapore
  local + regional HQs (Grab/Sea/GovTech/Stripe SG/…), Australia (Canva/Atlassian/…),
  and companies with an FDE tradition (Palantir/Anthropic/OpenAI/Databricks etc.)
- Validate every token during generation (request returns 200 with jobs); comment out
  invalid ones with a note
- The user reviews this list; later additions/removals touch only the YAML, not code

### 6.6 Optional extensions (backlog, not on the main line)
- HN "Who is Hiring" monthly threads (Algolia API) — early-signal source
- MyCareersFuture (Singapore) — deeper SG coverage

## 7. LLM Classification Module

- **Model**: `claude-haiku-4-5` ($1/$5 per MTok). The user explicitly chose the cheap
  model; do not upgrade it unilaterally
- **Invocation**: Anthropic Python SDK. **Prefer the Message Batches API** (50% discount,
  completes within 24h — plenty for a daily cron): accumulate the day's new jobs into one
  batch, collect results on the next run; when the day's count is small (<20), call
  synchronously instead. Put the fixed part of the classification prompt (taxonomy
  definitions + rules) in `system` with `cache_control`
- **Input**: title + company + location + description (truncated to 4000 chars —
  sufficient for classification)
- **Output**: structured outputs (`output_config.format` json_schema, or
  `messages.parse()` + Pydantic); the schema mirrors the classifications table.
  Never parse free text
- **Idempotency**: only process job_ids absent from classifications; use job_id as the
  batch `custom_id`
- **Cost estimate**: ~300 new jobs/day × ~1.2K input tokens (mostly the non-cached part)
  + ~150 output ≈ $0.5/day; ≈ $0.25/day with the Batch API ≈ **$8/month ceiling**;
  actual new-job volume will likely be lower
- **Cost-cutting fallback** (not for now): route obvious cases via title regex first,
  send only ambiguous titles to the LLM

### Eval (a portfolio highlight)
- `eval/labeled.jsonl`: sample 150-200 rows from real data, hand-label category
  (+ spot-check seniority)
- Stratified sampling: at least 15 per tier-1 category, including deliberate hard cases
  (MTS, Solutions Engineer, Research Engineer)
- `python -m jobmon.eval` outputs: overall accuracy, per-category precision/recall,
  confusion matrix
- Target: macro-F1 ≥ 0.85 across tier-1 categories; publish results in the README
- Any prompt change requires re-running the eval; log in `eval/RESULTS.md`
  (prompt version → metrics)

## 8. Alert Module (Telegram)

- Sending: `POST https://api.telegram.org/bot{TOKEN}/sendMessage` (MarkdownV2),
  chat_id is the user's private chat. TOKEN/CHAT_ID come from secrets
- **Instant alerts**: any new job matching a rule in `config/alert_rules.yaml`,
  pushed individually
- **Daily digest**: one summary message: new-posting counts per tier-1 category
  (by region), top new jobs (≤15, with links), anomaly signals (flag when a category's
  daily count exceeds its 30-day mean by 2σ)
- Idempotency via the `alerts_sent` table

```yaml
# config/alert_rules.yaml format
- name: anthropic-sg-any
  company: [Anthropic]           # optional, list, case-insensitive
  country: [SG]                  # optional
  category: null                 # optional, taxonomy slugs; null = any tier-1
- name: fde-anywhere
  category: [forward_deployed_engineer, agentic_engineer]
- name: sg-target-roles
  country: [SG]
  category: [data_scientist, ai_engineer, ml_engineer, data_analyst]
```
Fields within a rule are AND-ed; lists within a field are OR-ed. Tier-2/3 jobs never
trigger instant alerts.

## 9. Dashboard (Streamlit)

`dashboard/app.py`, multi-page app, read-only against `data/jobs.db`. Charts follow the
dataviz skill (the session implementing the dashboard should load that skill first).

**Page 1 — Job browser (job layer)**
- Filters: category (default: all tier-1), country, seniority, company, active flag, keyword
- Table: title / company / location / category / salary (if any) / first_seen / link;
  sorted by first_seen descending

**Page 2 — Market trends (market layer)**
- Weekly posting-volume trends per tier-1 category (line chart); software_developer
  overlaid as a gray baseline
- Three-region comparison (SG/US/AU, faceted or grouped)
- DA vs DS/MLE supply comparison (transition reference)

**Page 3 — Skills & emerging signals**
- Top-N skill frequencies and their weekly change (track agent-related terms closely:
  langgraph, mcp, agentic, …)
- Emerging-title detection: n-grams appearing in jobs.title for the first time and
  persisting afterward
- Time-to-fill distribution (closed jobs)

**Page 4 — Pipeline health** (internal): ingest_runs summary, last success per source,
classification backlog count

## 10. Repo Structure

```
job-market-monitor/
├── PLAN.md                      # Chinese version of this document
├── PLAN.en.md                   # this document
├── README.md                    # polished in Phase 5 (architecture diagram, eval results, screenshots)
├── pyproject.toml               # deps: httpx, pydantic, anthropic, pyyaml, streamlit, pytest, ruff
├── config/
│   ├── companies.yaml
│   ├── alert_rules.yaml
│   └── taxonomy.py              # taxonomy definitions (slug/tier/description), shared by prompt & dashboard
├── src/jobmon/
│   ├── run_pipeline.py          # entry point, orchestrates per §4
│   ├── db.py                    # schema creation + upsert + query helpers
│   ├── models.py                # Pydantic: NormalizedJob, Classification
│   ├── ingest/
│   │   ├── base.py              # adapter protocol: fetch(config) -> list[NormalizedJob]
│   │   ├── greenhouse.py / lever.py / ashby.py / adzuna.py
│   ├── classify/
│   │   ├── prompts.py           # system prompt (imports taxonomy.py, includes boundary rules)
│   │   └── classifier.py        # batch submit/collect + sync fallback
│   ├── alerts/
│   │   ├── rules.py
│   │   └── telegram.py
│   └── lifecycle.py             # active/closed determination, dedupe
├── dashboard/app.py
├── eval/
│   ├── labeled.jsonl
│   ├── run_eval.py
│   └── RESULTS.md
├── data/jobs.db                 # committed to the repo
├── tests/                       # adapters tested offline with fixture JSON; unit tests for db/lifecycle/rules
└── .github/workflows/pipeline.yml
```

## 11. Runtime Environment & Secrets

GitHub Actions secrets (locally: `.env`, gitignored):

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | LLM classification |
| `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` | Adzuna API |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Alerts |

Workflow essentials: cron twice daily (e.g. `0 1,13 * * *`); one source failing must not
block the others; commit `data/jobs.db` + push at the end (handle concurrency: retry
after `pull --rebase`); on a top-level pipeline exception → send a Telegram error
notification (self-monitoring).

## 12. Phased Execution Plan

### Phase 1 — Data Foundation `status: todo`
Highest priority; data starts accumulating the moment this ships.
1. Repo scaffolding: pyproject, directories, pytest/ruff, README placeholder
2. `models.py` + `db.py` (table creation, upsert, lifecycle)
3. Greenhouse/Lever/Ashby adapters + offline fixture tests
4. Seed company list (150-300, every token validated, per §6.5) → user review
5. Adzuna adapter (rate limiting + quota handling)
6. `run_pipeline.py` (ingest+upsert only, for now) + GitHub Actions workflow + DB commit-back
7. **Acceptance**: Actions runs green for 3 consecutive days; jobs table ≥ 3000 rows;
   all three countries have data; re-runs create no duplicates; when one source is
   deliberately cut off, the others still work

### Phase 2 — LLM Classification `status: todo`
1. `taxonomy.py` + classification prompt (boundary rules from §3 baked in)
2. Classifier (Batch API main path + sync fallback + idempotency)
3. Wire into the pipeline; run a one-off full classification of the accumulated backlog
4. Label 150-200 rows → get the eval running → iterate the prompt until tier-1
   macro-F1 ≥ 0.85
5. **Acceptance**: eval target met and recorded in RESULTS.md; projected monthly cost
   < $15; classification is idempotent

### Phase 3 — Telegram Alerts `status: todo`
1. Bot creation (manual, by the user: create the bot via BotFather, obtain chat_id)
   → configure secrets
2. Rules engine + instant push + daily digest + alerts_sent idempotency + failure notification
3. **Acceptance**: a crafted test job matching a rule produces a push; re-running the
   pipeline does not push again

### Phase 4 — Streamlit Dashboard `status: todo`
1. Four pages (§9); load the dataviz skill before implementing
2. Deploy to Streamlit Community Cloud
3. **Acceptance**: publicly reachable; filters/trend charts correct; readable on mobile

### Phase 5 — Portfolio Polish `status: todo`
1. README: motivation, architecture diagram (mermaid), eval results table, dashboard
   screenshots, design trade-offs (why ATS APIs over scraping, why SQLite-in-repo,
   why LLM over rules)
2. Backlog picks: HN Who's Hiring, fuzzy dedupe, weekly report (LLM-generated market
   summary), MyCareersFuture
3. **Acceptance**: the README lets an unfamiliar engineer understand the system and run
   it locally within 10 minutes

## 13. Engineering Conventions (all sessions must follow)

- Python 3.11+; type annotations; ruff format/lint; tests via `pytest`
- Adapters are pure functions: config in, `list[NormalizedJob]` out, no DB access;
  network errors raise `IngestError`, caught and logged by the orchestrator
- All timestamps stored as UTC ISO 8601 strings
- LLM calls: model pinned to `claude-haiku-4-5`; structured outputs; never parse free
  text; enable prompt caching for the fixed prompt portion
- No hardcoded secrets; never commit `.env`
- New dependencies need a clear justification; prefer the minimal set of
  stdlib + httpx + pydantic
- Commit messages in English, conventional style (feat:/fix:/chore:); the pipeline's
  auto-commit uses `chore(data): daily update`

## 14. Open Questions (non-blocking for Phase 1)

- [ ] Final composition of the seed company list — user reviews after Phase 1 generates it
- [ ] Initial alert_rules — user confirms before Phase 3
- [ ] Adzuna's actual quota — confirm after signup and backfill §6.4
- [ ] Emerging-title detection algorithm (simple n-gram first-appearance vs something
      more statistically rigorous) — decide in Phase 4
