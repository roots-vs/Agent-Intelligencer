# Wasatch Intelligence — Curation Tool

A local content intelligence platform for Longitude. Aggregates real estate news, agent movement signals, and market data for the Wasatch Back (Summit and Wasatch Counties, Utah). Runs entirely on your computer — no cloud accounts required.

Modeled on the Crowd Commerce architecture. Adapted for real estate.

---

## Quick Start

**Mac:**
```bash
bash run_mac.sh
```

**Windows:**
Double-click `run_windows.bat`

**Manual:**
```bash
python3 aggregator.py       # fetch latest RSS articles
python3 server.py           # open dashboard at http://localhost:8765
```

Your browser opens automatically at `http://localhost:8765`.

---

## What Each File Does

| File | Purpose |
|---|---|
| `feeds.py` | Feed registry — add/remove RSS sources here |
| `aggregator.py` | Fetches RSS feeds and saves articles to the database |
| `server.py` | Runs the local web dashboard |
| `dashboard.html` | The curation interface (served by server.py) |
| `curation.db` | SQLite database (auto-created on first run, not in git) |
| `agent_directory.py` | Agent/broker directory — import, search, export |
| `movement_tracker.py` | Monitors brokerage rosters for agent movements |
| `market_data.py` | Ingests and displays Wasatch Back market statistics |
| `rankings_tracker.py` | Agent performance rankings — import from RealTrends, UAR, etc. |
| `run_mac.sh` | One-click launcher for Mac |
| `run_windows.bat` | One-click launcher for Windows |

---

## Content Categories

| Category | What it covers |
|---|---|
| `national-brokerage` | News from Compass, Sotheby's, BHHS, Coldwell Banker, RE/MAX, eXp, etc. |
| `local-market` | Wasatch Back listing activity, development, resort RE news |
| `agent-intel` | Agent movements, team formations, production rankings |
| `industry-practice` | Best practices for buyer/seller rep, marketing, tech tools |
| `market-data` | Median prices, days on market, inventory, YoY comparisons |
| `comp-market` | Aspen, Jackson Hole, Sun Valley, Steamboat — benchmark markets |

Each article also carries a **Market Area** tag: `Wasatch Back`, `National`, or `Comp Market`.

---

## The Review Workflow

1. **Run the aggregator** — new articles land with status `new`
2. **Open the dashboard** — filter, search, and review article cards
3. **Mark articles** using the buttons:
   - **★ Queue** — goes into the next content piece
   - **Save** — worth reading again, not decided
   - **Skip** — not relevant, hide it
4. **Open Compose** → select content type, audience, zone, calendar moment
5. **Generate Claude Prompt** → copy and paste into your Wasatch Intelligence GPT
6. **Archive sources** with an article title tag when done

---

## Adding RSS Feeds

Open `feeds.py` and add a line to the `FEEDS` list:

```python
("Publication Name", "https://example.com/feed/", "category", "notes"),
```

For sources without RSS feeds, add an entry to `NO_RSS_SOURCES` with instructions for setting up an rss.app or Kill the Newsletter workaround.

---

## Agent Directory

The `agents` table in `curation.db` stores licensed agents and brokers in Summit and Wasatch counties.

```bash
# Import from a CSV (columns: name, brokerage, office, county, email, phone, license_num, license_type, source)
python3 agent_directory.py --import agents.csv

# Search by name, brokerage, or office
python3 agent_directory.py --search 'Summit Sotheby'

# Export to CSV
python3 agent_directory.py --export agents_export.csv

# Show summary stats
python3 agent_directory.py --stats
```

**Recommended data sources:**
- Utah Division of Real Estate licensee lookup: [realestate.utah.gov](https://realestate.utah.gov)
- Brokerage roster pages (summitsir.com, utahrealestate.com, compass.com/agents/park-city-ut)
- Realtor.com agent search by zip (84060, 84098, 84032)

---

## Agent Movement Tracker

Monitors brokerage roster pages for new or departed agents. Logs changes as `agent-intel` articles in the curation database.

```bash
python3 movement_tracker.py                          # check all rosters
python3 movement_tracker.py --dry-run                # see changes without saving
python3 movement_tracker.py --brokerage "Compass"    # check one brokerage
```

**First run:** saves a baseline snapshot. No articles are created until the second run detects a difference.

**Note:** Roster page structures change. If a brokerage shows 0 names extracted, inspect the page and update the `extract_names()` function in `movement_tracker.py`. Installing `beautifulsoup4` (`pip3 install beautifulsoup4`) improves name extraction accuracy.

---

## Market Data

Imports and displays Wasatch Back real estate market statistics (median price, days on market, inventory, etc.).

```bash
# Import from a CSV
python3 market_data.py --import stats.csv

# Show latest stats for an area
python3 market_data.py --show 'Summit County'
python3 market_data.py --show 'Park City 84060'

# Show all areas
python3 market_data.py --show-all

# Generate a GPT prompt capsule (for use in Compose)
python3 market_data.py --capsule 'Summit County'
```

**CSV format:**
```
area, metric, value, period, source, source_url, notes
Summit County, median_sale_price, 1250000, 2026-Q1, Utah Association of Realtors, https://..., 
```

**Available metrics:** `median_sale_price` | `avg_sale_price` | `days_on_market` | `active_listings` | `closed_sales` | `months_supply` | `list_to_sale_ratio`

**Data sources:**
- Utah Association of Realtors: [utahrealtors.com/news](https://www.utahrealtors.com/news)
- Park City Board of Realtors: [pcbr.com](https://www.pcbr.com)

---

## Agent Performance Rankings

Import verified production data from RealTrends, Utah Association of Realtors, and other sources. The importer auto-flags Wasatch Back agents and generates an `agent-intel` article in the dashboard.

```bash
# Preview a CSV import without saving
python3 rankings_tracker.py --import ~/Downloads/realtrends_utah_2025.csv --source realtrends --year 2025 --dry-run

# Import and generate dashboard article
python3 rankings_tracker.py --import ~/Downloads/realtrends_utah_2025.csv --source realtrends --year 2025

# Show only Wasatch Back agents across all sources
python3 rankings_tracker.py --wasatch-back

# Show all sources and record counts
python3 rankings_tracker.py --stats
```

**Getting RealTrends data:** Visit [realtrends.com/ranking/best-real-estate-agents-utah/individuals-by-volume/](https://www.realtrends.com/ranking/best-real-estate-agents-utah/individuals-by-volume/) (published annually ~May). Copy agent rows into a CSV with columns: `rank, agent_name, brokerage, city, state, volume_dollars, units`.

**Supported sources:** `realtrends` | `uar` | `real_producers` | `summit_sir` | `homelight` | `manual`

---

## Newsletter Feeds (Kill the Newsletter)

Several sources are email-only newsletters. We use [Kill the Newsletter](https://kill-the-newsletter.com) to convert them to RSS:

1. Go to kill-the-newsletter.com → create a feed → get a `@kill-the-newsletter.com` email
2. Subscribe to the newsletter using that email
3. Paste the generated Atom URL into `feeds.py` under `FEEDS`

**Currently configured:** The Real Deal · BAM/Broke Agent · Inman Morning Briefing · RealTrends The Broker · Tom Ferry Newsletter

---

## Automating Daily Fetches

### Mac (cron)
```bash
crontab -e
```
Add:
```
0 7 * * * cd /path/to/curation-tool && python3 aggregator.py >> aggregator.log 2>&1
0 8 * * 1 cd /path/to/curation-tool && python3 movement_tracker.py >> movement.log 2>&1
```
This fetches new articles every morning at 7 AM, and checks agent movement every Monday at 8 AM.

### Windows (Task Scheduler)
1. Open Task Scheduler → Create Basic Task → Daily → 7:00 AM
2. Action: Start a program → `python` → Arguments: `aggregator.py` → Start in: path to curation-tool folder

---

## Signal Priority

From the Wasatch Intelligence charter — use this when multiple signals arrive on the same day:

| Priority | Act when | Examples |
|---|---|---|
| **1 — Same day** | Agent movement confirmed. New official market data. Brokerage opening/closing. | Agent leaves Summit Sotheby's for Compass |
| **2 — Within 48h** | National brokerage tech with local implications. Comp market data. Top-producer profile. | Compass launches AI listing tool |
| **3 — This week** | Industry practice content. Tool spotlights. Local lifestyle content. | Best practices for contingency offers |
| **4 — Background file** | Historical data. Out-of-market news without local implications. | General RE thought leadership |

---

## Troubleshooting

**"No articles found" in dashboard:**
Run `python3 aggregator.py` first. The database needs to be populated.

**A feed shows "Network error":**
The RSS URL may have changed. Check the publication's website and update `feeds.py`.

**Dashboard won't open:**
Check that nothing else is using port 8765. Change with `python3 server.py --port 9000`.

**Python not found:**
Install Python 3 from [python.org](https://www.python.org/downloads/). No packages required — this tool uses only Python's standard library (except optionally `beautifulsoup4` for movement_tracker.py).
