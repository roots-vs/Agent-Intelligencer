# Wasatch Intelligence — Agent Intelligencer
### CLAUDE.md — Project briefing for Claude Code

This file is read automatically at the start of every Claude Code session.
It contains everything needed to resume work without re-explanation.

---

## What This Is

**Wasatch Intelligence** is a local content intelligence platform for
**Longitude**, a real estate intelligence company focused on the Wasatch Back
market (Park City, Summit County, Wasatch County, Heber Valley, Utah).

It does three things:
1. **Aggregates** real estate news and trade media from 29 RSS feeds into a
   local SQLite database (`curation.db`)
2. **Cross-references** that content against Longitude's agent database
   (3,669 agents in PostgreSQL) to surface relevant signals per agent
3. **Outputs** curated intelligence — a curation dashboard, personalized
   agent digests, and a weekly market newsletter

Modeled on an internal tool called "Crowd Commerce." Adapted for real estate.

---

## Architecture Overview

```
Data Sources
  ├── 29 RSS/Atom feeds (aggregator.py)         → curation.db articles table
  ├── Longitude Network PostgreSQL (longitude_sync.py) → article_agent_links, longitude_agents
  ├── RealTrends / UAR rankings CSVs (rankings_tracker.py) → agent_rankings table
  └── Market stats CSVs (market_data.py)        → market_stats table

Central Hub
  └── curation.db (SQLite) ← everything lands here

Output Layer (partially built)
  ├── Curation Dashboard  (server.py + dashboard.html) — LIVE at localhost:8765
  ├── digest_generator.py — TO BUILD: weekly HTML email per agent
  ├── newsletter_engine.py — TO BUILD: weekly Wasatch Back market newsletter
  └── longitude_sync.py v2 — TO BUILD: write visibility scores back to PostgreSQL
```

---

## File Map

| File | Status | Purpose |
|---|---|---|
| `feeds.py` | ✅ Live | RSS feed registry (29 sources, 6 categories) |
| `aggregator.py` | ✅ Live | Fetches all feeds → saves to curation.db |
| `server.py` | ✅ Live | HTTP server for dashboard at localhost:8765 |
| `dashboard.html` | ✅ Live | Curation review UI |
| `longitude_sync.py` | ✅ Live | PostgreSQL ↔ SQLite bridge + topic tagging |
| `agent_directory.py` | ✅ Live | Import/search/export agent CSV data |
| `movement_tracker.py` | ✅ Live | Scrapes brokerage rosters for agent moves |
| `market_data.py` | ✅ Live | Ingest and display market statistics |
| `rankings_tracker.py` | ✅ Live | Import RealTrends/UAR performance data |
| `run_mac.sh` | ✅ Live | One-click Mac launcher |
| `digest_generator.py` | ❌ Not built | Weekly per-agent HTML email digest |
| `newsletter_engine.py` | ❌ Not built | Weekly Wasatch Back market newsletter |
| `Dockerfile` | ❌ Not built | For Google Cloud Run deployment |

---

## Database: curation.db (SQLite)

**Primary tables** — created by `aggregator.py`:

```sql
articles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    uid         TEXT UNIQUE NOT NULL,     -- SHA1 of "source::link"
    source      TEXT NOT NULL,            -- feed label e.g. "Inman News"
    category    TEXT NOT NULL,            -- see Category Taxonomy below
    market_area TEXT,                     -- 'Wasatch Back' | 'National' | 'Comp Market'
    title       TEXT NOT NULL,
    link        TEXT NOT NULL,
    summary     TEXT,                     -- HTML-stripped, max 600 chars
    published   TEXT,                     -- ISO datetime UTC
    fetched_at  TEXT NOT NULL,            -- ISO datetime UTC
    status      TEXT DEFAULT 'new',       -- new | reviewed | selected | skip | archived
    issue_tag   TEXT,                     -- e.g. "April 2026 Market Update"
    notes       TEXT                      -- free text; longitude_sync appends topic: tags
)

fetch_log (
    id INTEGER PRIMARY KEY,
    source TEXT, run_at TEXT, items_added INTEGER, error TEXT
)
```

**Integration tables** — created by `longitude_sync.py`:

```sql
article_agent_links (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    article_uid TEXT NOT NULL,            -- FK → articles.uid
    agent_email TEXT NOT NULL,            -- FK → longitude_agents.email
    agent_name  TEXT NOT NULL,
    match_type  TEXT NOT NULL,            -- 'exact' | 'fuzzy' | 'manual'
    match_score REAL DEFAULT 1.0,
    created_at  TEXT NOT NULL,
    UNIQUE(article_uid, agent_email)
)

longitude_agents (
    email               TEXT PRIMARY KEY,
    full_name           TEXT,
    brokerage_name      TEXT,
    specialty           TEXT,
    zillow_sales_12mo   INTEGER,
    zillow_price_range_max REAL,
    realtrends_rank     INTEGER,
    ig_handle           TEXT,
    agent_website       TEXT,
    zillow_service_areas TEXT,
    last_synced         TEXT
)
```

**Other tables** created by their respective modules:

```sql
-- rankings_tracker.py
agent_rankings (rank, agent_name, brokerage, city, state,
                volume_dollars, units, year, source_id,
                wasatch_back_flag, article_uid)
ranking_sources (id, name, year, import_date, record_count)

-- market_data.py
market_stats (id, area, metric, value, period, source,
              source_url, notes, fetched_at)
```

---

## Database: Longitude Network (PostgreSQL)

```
host: localhost   port: 5433   db: longitude_network
user: longitude   password: longitude_dev_2024
```

Override with env vars: `PG_HOST`, `PG_PORT`, `PG_DBNAME`, `PG_USER`, `PG_PASSWORD`

**agents table** (3,669 rows + 46 ecosystem contacts):

```sql
agents (
    full_name           TEXT,
    email               TEXT,
    brokerage_name      TEXT,
    specialty           TEXT,
    ig_handle           TEXT,
    zillow_sales_12mo   INTEGER,    -- transactions in last 12 months (Zillow)
    zillow_price_range_max REAL,    -- highest price point (Zillow)
    realtrends_rank     INTEGER,    -- Utah ranking from RealTrends (annual)
    bio_text            TEXT,
    agent_website       TEXT,
    zillow_service_areas JSON       -- cast to ::text when using in COALESCE
)
```

**Important:** `zillow_service_areas` is a JSON column. Always cast:
```sql
COALESCE(zillow_service_areas::text, '') AS zillow_service_areas
```

---

## Content Category Taxonomy

| Category | Market Area | What it covers |
|---|---|---|
| `national-brokerage` | National | Compass, Sotheby's, BHHS, RE/MAX, eXp news |
| `local-market` | Wasatch Back | Park City listings, development, resort RE |
| `agent-intel` | Wasatch Back | Agent moves, rankings, market signals for agents |
| `industry-practice` | National | Selling strategy, CRM, marketing, tech tools |
| `market-data` | Wasatch Back | Median prices, DOM, inventory, YoY stats |
| `comp-market` | Comp Market | Aspen, Jackson Hole, Sun Valley, Steamboat |

`market_area` is set automatically from `category` in `aggregator.py`:
```python
MARKET_AREA_MAP = {
    "local-market":       "Wasatch Back",
    "agent-intel":        "Wasatch Back",
    "market-data":        "Wasatch Back",
    "national-brokerage": "National",
    "industry-practice":  "National",
    "comp-market":        "Comp Market",
}
```

---

## Server API (server.py)

Base URL: `http://localhost:8765`

| Endpoint | Method | Params | Returns |
|---|---|---|---|
| `/` or `/dashboard` | GET | — | dashboard.html |
| `/api/articles` | GET | `status`, `category`, `days`, `market` | article list |
| `/api/stats` | GET | — | counts by status, last fetch time |
| `/api/export` | GET | — | all `status='selected'` articles |
| `/api/market-stats` | GET | — | latest 20 market_stats rows |
| `/api/update` | POST | `{id, status, issue_tag, notes}` | `{ok: true}` |
| `/api/bulk-update` | POST | `{ids: [], status}` | `{ok, updated}` |

---

## longitude_sync.py — How It Works

Runs four passes in sequence:

**Pass 1 — Topic Relevance Tagging** (no PG connection needed)
- Scans all articles from the last 90 days for keyword matches
- 6 topic clusters: Wasatch Back Development, Market Conditions & Interest Rates,
  AI & Technology for Agents, Luxury & Resort Market Signals,
  Brokerage & Industry Moves, Selling Strategy & Best Practices
- Matching articles have `notes` field updated with `topic:ClusterName`
- High-signal matches are re-categorized to `agent-intel` so they surface in that tab

**Pass 2 — Agent Name Detection**
- Loads all agents from PostgreSQL
- Builds a name index (normalized lowercase)
- Scans article title + summary for exact substring matches (fast path)
- Falls back to fuzzy SequenceMatcher (ratio ≥ 0.82) for 8+ char names
- Writes matches to `article_agent_links`

**Pass 3 — Performance Snapshot Articles**
- Pulls agents where `_is_wasatch_back_agent()` is True (checks brokerage + service areas for Park City / Summit County / Heber etc. keywords)
- Generates 3 article types: RealTrends ranked agents summary, Zillow top producers, individual VIP profiles
- Inserts as `agent-intel` / `Wasatch Back` articles with `source = 'Longitude Network'`

**Pass 4 — Network Stats Article**
- Counts agents per brokerage in Wasatch Back
- Creates one `market-data` article with brokerage market share

CLI flags:
```bash
python3 longitude_sync.py                  # full sync
python3 longitude_sync.py --topics         # topic tagging only (no PG needed)
python3 longitude_sync.py --scan-articles  # topic + name scan only
python3 longitude_sync.py --performance-snapshot
python3 longitude_sync.py --network-stats
python3 longitude_sync.py --dry-run        # preview without saving
python3 longitude_sync.py --stats          # show link counts
python3 longitude_sync.py --agent "Name"   # look up one agent
```

---

## Daily Workflow

```bash
python3 aggregator.py       # 1. fetch latest RSS articles (~2 min)
python3 longitude_sync.py   # 2. cross-reference agents + tag topics
python3 server.py           # 3. open dashboard at localhost:8765
```

Automate with cron (Mac):
```
0 7 * * * cd /path/to/curation-tool && python3 aggregator.py >> aggregator.log 2>&1
0 7 * * * cd /path/to/curation-tool && python3 longitude_sync.py >> sync.log 2>&1
0 8 * * 1 cd /path/to/curation-tool && python3 movement_tracker.py >> movement.log 2>&1
```

---

## Signal Priority (from Wasatch Intelligence charter)

| Priority | Act when | Examples |
|---|---|---|
| 1 — Same day | Agent movement confirmed. New official market data. | Agent leaves Summit SIR for Compass |
| 2 — Within 48h | National brokerage tech with local implications. Comp market data. | Compass launches AI listing tool |
| 3 — This week | Industry practice content. Tool spotlights. | Best practices for contingency offers |
| 4 — Background | Historical data. Out-of-market news. | General RE thought leadership |

---

## What to Build Next

### 1. `digest_generator.py` — Personalized agent email digests (HIGHEST PRIORITY)

Build a weekly HTML email digest per agent. The data is ready:
- `article_agent_links` links agents to articles where they're mentioned
- `longitude_agents` has email, brokerage, ig_handle
- `articles` has title, link, summary, category, published

Structure per digest:
- Intro: "{agent_name}, here's your Wasatch Back intelligence brief for this week"
- Section 1: Articles mentioning the agent by name (from article_agent_links)
- Section 2: Top local-market and agent-intel articles from their market area
- Section 3: One national brokerage or industry-practice article
- Footer: link to unsubscribe / "powered by Longitude Intelligence"

Output: one `.html` file per agent in an `output/digests/` folder, named by email.
Later: plug into beehiiv or SendGrid for automated delivery.

CLI target:
```bash
python3 digest_generator.py                        # generate all agent digests
python3 digest_generator.py --agent matt@email.com # one agent
python3 digest_generator.py --preview              # render first 3 agents, open in browser
python3 digest_generator.py --days 7               # articles from last N days
```

### 2. `newsletter_engine.py` — Weekly Wasatch Back market newsletter

A single weekly HTML roundup for the full agent network (not personalized).
Format: 6 sections (one per category), top 2-3 articles each, clean HTML.
Output: `output/newsletter_YYYY-MM-DD.html` — paste directly into beehiiv.

### 3. Google Cloud Run deployment

Files needed:
- `Dockerfile`
- `.dockerignore`
- `cloudbuild.yaml` (optional)

Target: `gcloud run deploy wasatch-intelligence --project imposing-avatar-494913-u3`
Auth: Cloud IAP restricted to `@rootsvs.com` Google accounts
Scheduler: Cloud Scheduler to run aggregator + longitude_sync daily at 7am MT

### 4. `longitude_sync.py` v2 — Write signals back to PostgreSQL

Close the loop: article mention count → `visibility_score` in PostgreSQL agents table.
When an agent gets 3+ article mentions in a week, flag them for outreach.

---

## Conventions & Patterns

- **UIDs**: SHA1 of `"source::link"` — see `uid()` in aggregator.py. Use same pattern in all modules.
- **Timestamps**: Always UTC ISO format. Use `now_iso()` from longitude_sync.py.
- **Auto-generated articles**: Use `source = 'Longitude Network'`, `link = '#longitude-sync'`
- **No external dependencies for core**: Standard library only. psycopg2 and beautifulsoup4 are optional.
- **XML sanitization**: Three-pass cleaner in `sanitize_xml()` handles control chars, trailing junk, unescaped `&`. Use it for any RSS parsing.
- **Port**: Dashboard runs on `8765`. If occupied: `lsof -ti:8765 | xargs kill -9`

---

## GitHub

Repo: `https://github.com/roots-vs/Agent-Intelligencer`

---

## Key People & Context

- **Matt Sanders** (matt@rootsvs.com) — founder, Roots Venture Studio / Longitude
- **Longitude** — real estate intelligence platform for the Wasatch Back market
- **Wasatch Back** = Park City + Summit County + Wasatch County + Heber Valley, Utah
- **Comp markets**: Aspen, Jackson Hole, Sun Valley, Steamboat Springs
- **Key brokerages**: Summit Sotheby's (dominant), Compass, BHHS Utah, Engel & Völkers, Coldwell Banker, KW Park City

---

## Known Issues & Gotchas

- `zillow_service_areas` in PostgreSQL is a JSON column — always cast with `::text` in COALESCE
- Port 8765 conflicts with Crowd Commerce (an older internal tool) — kill it before starting
- Some RSS feeds require the XML sanitizer (Tom Ferry, RE/MAX) — always route through `detect_and_parse()`
- rss.app feeds sometimes return 404 when the feed ID has been regenerated — check notes in feeds.py
- Kill the Newsletter feeds are empty until first email delivery from the newsletter publisher
- The `days` filter in the dashboard defaults to 14 days — set to "All time" to see market-data articles which may be older
