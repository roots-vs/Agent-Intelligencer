#!/usr/bin/env python3
"""
rankings_tracker.py — Utah Mountain Living — Longitude Market Intelligence Agent Performance Rankings
----------------------------------------------------------------------
Stores and queries agent performance data from trusted annual/periodic
ranking sources. Since most ranking pages are JavaScript-rendered and
not RSS-accessible, this module supports structured CSV import plus
automatic article generation for the curation dashboard.

Supported sources (add more as discovered):
  • RealTrends Verified — annual, MLS-verified production rankings
  • Utah Association of Realtors — annual award recipients
  • Real Producers SLC — monthly top-500 agent profiles (Salt Lake market)
  • Brokerage press releases — Summit SIR, BHHS annual top-producer lists
  • Inman lists — occasional top-producer features

Workflow for RealTrends (annual, ~May each year):
  1. Go to: realtrends.com/ranking/best-real-estate-agents-utah/individuals-by-volume/
  2. Use browser DevTools → Network tab → find the JSON data request, or
     manually copy visible agent rows into a CSV with columns:
     rank, agent_name, brokerage, city, state, volume_dollars, units
  3. Run: python3 rankings_tracker.py --import path/to/file.csv \\
             --source realtrends --year 2025 --ranked-by volume
  4. Module cross-references Wasatch Back agents, updates agent_directory,
     and generates an agent-intel article in the curation dashboard.

Usage:
    python3 rankings_tracker.py --import FILE --source SOURCE --year YEAR
    python3 rankings_tracker.py --list [--source SOURCE] [--year YEAR]
    python3 rankings_tracker.py --wasatch-back [--year YEAR]
    python3 rankings_tracker.py --generate-article --source SOURCE --year YEAR
    python3 rankings_tracker.py --stats
"""

import sqlite3
import csv
import argparse
import os
import sys
import hashlib
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(__file__), "curation.db")

# Cities/areas considered Wasatch Back
WASATCH_BACK_CITIES = {
    "park city", "heber city", "heber", "midway", "kamas", "oakley",
    "coalville", "francis", "peoa", "wanship", "snyderville", "silver summit",
    "jeremy ranch", "kimball junction", "deer valley", "empire pass",
    "promontory", "hideout", "daniel", "charleston", "wallsburg",
}

# Known source metadata
SOURCE_META = {
    "realtrends": {
        "display_name": "RealTrends Verified",
        "url": "https://www.realtrends.com/ranking/best-real-estate-agents-utah/individuals-by-volume/",
        "cadence": "Annual (published ~May, covers prior year closings)",
        "verified": True,
        "verification_method": "MLS transaction data cross-check",
        "notes": "Gold standard for agent production. Agents must apply; RealTrends verifies against MLS. New program launching June 2026.",
    },
    "uar": {
        "display_name": "Utah Association of Realtors",
        "url": "https://www.utahrealtors.com/awards",
        "cadence": "Annual",
        "verified": True,
        "verification_method": "UAR member data",
        "notes": "Annual awards including Triple Crown (top production thresholds). Summit/Wasatch County recipients are high-value.",
    },
    "real_producers": {
        "display_name": "Real Producers — Salt Lake City",
        "url": "https://realproducersmag.com",
        "cadence": "Monthly profiles",
        "verified": True,
        "verification_method": "MLS-based selection (top 500 by volume in market)",
        "notes": "Covers SLC market. May include Wasatch Back agents. Profile-based, not a ranked list.",
    },
    "summit_sir": {
        "display_name": "Summit Sotheby's — Internal Top Producer List",
        "url": "https://summitsothebysrealty.com",
        "cadence": "Annual (press release or internal communication)",
        "verified": False,
        "verification_method": "Self-reported by brokerage",
        "notes": "Annual top-producer recognition. High value for agent-intel; brokerage-verified but not third-party.",
    },
    "inman": {
        "display_name": "Inman — Agent Rankings & Lists",
        "url": "https://www.inman.com",
        "cadence": "Ad hoc (multiple times per year)",
        "verified": False,
        "verification_method": "Editorial selection, self-reported or MLS-sampled",
        "notes": "Inman publishes various 'top agent' lists throughout the year. Filter for Utah/mountain west.",
    },
    "homelight": {
        "display_name": "HomeLight — Top Agents (Park City)",
        "url": "https://www.homelight.com/park-city-ut-real-estate-agents",
        "cadence": "Ongoing (algorithm-updated)",
        "verified": True,
        "verification_method": "Transaction data analysis",
        "notes": "Algorithm-based ranking using transaction history. Useful directional signal for rising agents.",
    },
    "manual": {
        "display_name": "Manual Entry",
        "url": "",
        "cadence": "As needed",
        "verified": False,
        "verification_method": "Manual research",
        "notes": "Manually entered ranking or performance data.",
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Database setup
# ─────────────────────────────────────────────────────────────────────────────

def init_tables(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS agent_rankings (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            source          TEXT    NOT NULL,
            year            INTEGER NOT NULL,
            ranked_by       TEXT    NOT NULL DEFAULT 'volume',
            rank_position   INTEGER,
            agent_name      TEXT    NOT NULL,
            brokerage       TEXT,
            city            TEXT,
            state           TEXT    DEFAULT 'UT',
            volume_dollars  REAL,
            units           INTEGER,
            notes           TEXT,
            wasatch_back    INTEGER DEFAULT 0,  -- 1 if in Wasatch Back market area
            imported_at     TEXT    NOT NULL,
            uid             TEXT    UNIQUE NOT NULL
        );

        CREATE TABLE IF NOT EXISTS ranking_sources (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            source          TEXT    UNIQUE NOT NULL,
            display_name    TEXT,
            last_imported   TEXT,
            latest_year     INTEGER,
            record_count    INTEGER DEFAULT 0,
            notes           TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_rankings_source_year
            ON agent_rankings (source, year);
        CREATE INDEX IF NOT EXISTS idx_rankings_wasatch
            ON agent_rankings (wasatch_back, year);
        CREATE INDEX IF NOT EXISTS idx_rankings_agent
            ON agent_rankings (agent_name);
    """)
    conn.commit()


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    init_tables(conn)
    return conn


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def is_wasatch_back(city: str) -> bool:
    if not city:
        return False
    return city.strip().lower() in WASATCH_BACK_CITIES


def make_uid(source, year, agent_name, ranked_by):
    raw = f"{source}|{year}|{ranked_by}|{agent_name.lower().strip()}"
    return hashlib.sha1(raw.encode()).hexdigest()


def parse_dollars(val):
    """Parse '$1,234,567' or '1234567' or '1.2M' to float."""
    if not val:
        return None
    val = str(val).strip().replace(",", "").replace("$", "")
    if val.upper().endswith("M"):
        try:
            return float(val[:-1]) * 1_000_000
        except ValueError:
            return None
    if val.upper().endswith("B"):
        try:
            return float(val[:-1]) * 1_000_000_000
        except ValueError:
            return None
    try:
        return float(val)
    except ValueError:
        return None


def parse_units(val):
    if not val:
        return None
    try:
        return int(str(val).strip().replace(",", ""))
    except ValueError:
        return None


def format_dollars(val):
    if val is None:
        return "N/A"
    if val >= 1_000_000_000:
        return f"${val/1_000_000_000:.2f}B"
    if val >= 1_000_000:
        return f"${val/1_000_000:.1f}M"
    return f"${val:,.0f}"


# ─────────────────────────────────────────────────────────────────────────────
# Import
# ─────────────────────────────────────────────────────────────────────────────

COLUMN_ALIASES = {
    # rank
    "rank": "rank_position", "#": "rank_position", "position": "rank_position",
    # name
    "name": "agent_name", "agent": "agent_name", "agent name": "agent_name",
    "full name": "agent_name",
    # brokerage
    "brokerage": "brokerage", "company": "brokerage", "firm": "brokerage",
    "office": "brokerage", "team/company": "brokerage",
    # city
    "city": "city", "location": "city", "market": "city",
    # state
    "state": "state",
    # volume
    "volume": "volume_dollars", "volume_dollars": "volume_dollars",
    "sales volume": "volume_dollars", "dollar volume": "volume_dollars",
    "total volume": "volume_dollars",
    # units
    "units": "units", "transactions": "units", "sides": "units",
    "closed units": "units", "transaction sides": "units",
    # notes
    "notes": "notes", "note": "notes",
}


def normalize_header(h):
    return COLUMN_ALIASES.get(h.strip().lower(), h.strip().lower())


def import_csv(filepath, source, year, ranked_by="volume", dry_run=False):
    """
    Import agent rankings from a CSV file.
    Expected columns (flexible, uses COLUMN_ALIASES for mapping):
      rank, agent_name, brokerage, city, state, volume_dollars, units, notes
    """
    if not os.path.exists(filepath):
        print(f"  ✗ File not found: {filepath}")
        return 0

    now = datetime.now(timezone.utc).isoformat()
    conn = get_conn()
    rows_imported = 0
    rows_skipped = 0
    wasatch_count = 0

    with open(filepath, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        raw_headers = reader.fieldnames or []
        headers = {h: normalize_header(h) for h in raw_headers}

        print(f"\n  Importing: {filepath}")
        print(f"  Source: {source} | Year: {year} | Ranked by: {ranked_by}")
        print(f"  Columns detected: {[headers[h] for h in raw_headers]}")
        print()

        for i, raw_row in enumerate(reader, start=1):
            row = {headers[k]: v for k, v in raw_row.items()}

            agent_name = row.get("agent_name", "").strip()
            if not agent_name:
                continue

            city = row.get("city", "").strip()
            state = row.get("state", "UT").strip() or "UT"
            brokerage = row.get("brokerage", "").strip()
            rank_pos = parse_units(row.get("rank_position"))
            volume = parse_dollars(row.get("volume_dollars"))
            units = parse_units(row.get("units"))
            notes = row.get("notes", "").strip() or None
            wb = 1 if is_wasatch_back(city) else 0
            uid = make_uid(source, year, agent_name, ranked_by)

            if wb:
                wasatch_count += 1

            if dry_run:
                marker = "🏔️ " if wb else "   "
                vol_str = format_dollars(volume)
                print(f"  {marker}#{rank_pos or i:>4}  {agent_name:<35} {brokerage:<30} {city:<20} {vol_str}")
                continue

            try:
                conn.execute("""
                    INSERT OR REPLACE INTO agent_rankings
                        (source, year, ranked_by, rank_position, agent_name,
                         brokerage, city, state, volume_dollars, units,
                         notes, wasatch_back, imported_at, uid)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (source, year, ranked_by, rank_pos, agent_name,
                      brokerage, city, state, volume, units,
                      notes, wb, now, uid))
                rows_imported += 1
            except sqlite3.IntegrityError:
                rows_skipped += 1

    if not dry_run:
        # Update ranking_sources summary
        meta = SOURCE_META.get(source, {})
        conn.execute("""
            INSERT INTO ranking_sources (source, display_name, last_imported, latest_year, record_count, notes)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(source) DO UPDATE SET
                last_imported = excluded.last_imported,
                latest_year   = MAX(latest_year, excluded.latest_year),
                record_count  = record_count + excluded.record_count
        """, (source, meta.get("display_name", source), now, year,
              rows_imported, meta.get("notes", "")))
        conn.commit()
        print(f"  ✓ Imported {rows_imported} agents ({wasatch_count} Wasatch Back) | {rows_skipped} duplicates skipped")
    else:
        print(f"\n  [dry-run] {i} rows | {wasatch_count} Wasatch Back agents flagged")

    conn.close()
    return rows_imported


# ─────────────────────────────────────────────────────────────────────────────
# Query & display
# ─────────────────────────────────────────────────────────────────────────────

def list_rankings(source=None, year=None, wasatch_only=False, limit=50):
    conn = get_conn()
    conditions = []
    params = []
    if source:
        conditions.append("source = ?")
        params.append(source)
    if year:
        conditions.append("year = ?")
        params.append(year)
    if wasatch_only:
        conditions.append("wasatch_back = 1")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    rows = conn.execute(f"""
        SELECT rank_position, agent_name, brokerage, city, state,
               volume_dollars, units, source, year, wasatch_back
        FROM agent_rankings
        {where}
        ORDER BY year DESC, source, rank_position ASC NULLS LAST
        LIMIT ?
    """, params + [limit]).fetchall()
    conn.close()

    if not rows:
        print("  No rankings found.")
        return

    print(f"\n  {'#':>4}  {'Agent':<35} {'Brokerage':<28} {'City':<18} {'Volume':>12}  {'Units':>5}  Source/Year")
    print("  " + "─" * 115)
    for r in rows:
        marker = "🏔️" if r["wasatch_back"] else "  "
        vol = format_dollars(r["volume_dollars"])
        print(f"  {marker}{r['rank_position'] or '?':>4}  {r['agent_name']:<35} {(r['brokerage'] or ''):<28} {(r['city'] or ''):<18} {vol:>12}  {r['units'] or '?':>5}  {r['source']}/{r['year']}")


def show_wasatch_back(year=None):
    """Display all Wasatch Back agents across all sources, optionally filtered by year."""
    conn = get_conn()
    params = []
    year_clause = ""
    if year:
        year_clause = "AND year = ?"
        params.append(year)

    rows = conn.execute(f"""
        SELECT rank_position, agent_name, brokerage, city, volume_dollars,
               units, source, year, ranked_by
        FROM agent_rankings
        WHERE wasatch_back = 1 {year_clause}
        ORDER BY year DESC, volume_dollars DESC NULLS LAST
    """, params).fetchall()
    conn.close()

    if not rows:
        print("  No Wasatch Back agents found in rankings database.")
        print("  Import data first: python3 rankings_tracker.py --import FILE --source realtrends --year 2025")
        return

    print(f"\n  WASATCH BACK AGENTS — Verified Performance Rankings")
    print(f"  {'#':>4}  {'Agent':<35} {'Brokerage':<28} {'City':<18} {'Volume':>12}  {'Units':>5}  Source/Year")
    print("  " + "─" * 115)
    for r in rows:
        vol = format_dollars(r["volume_dollars"])
        print(f"  {r['rank_position'] or '?':>4}  {r['agent_name']:<35} {(r['brokerage'] or ''):<28} {(r['city'] or ''):<18} {vol:>12}  {r['units'] or '?':>5}  {r['source']}/{r['year']}")


def show_stats():
    conn = get_conn()
    sources = conn.execute("""
        SELECT source, display_name, last_imported, latest_year, record_count, notes
        FROM ranking_sources ORDER BY latest_year DESC
    """).fetchall()

    total = conn.execute("SELECT COUNT(*) FROM agent_rankings").fetchone()[0]
    wasatch = conn.execute("SELECT COUNT(*) FROM agent_rankings WHERE wasatch_back=1").fetchone()[0]
    conn.close()

    print(f"\n  RANKINGS DATABASE SUMMARY")
    print(f"  Total records: {total} | Wasatch Back agents: {wasatch}")
    print()

    if sources:
        print(f"  {'Source':<20} {'Year':>6}  {'Records':>8}  {'Last Imported':<25}  Notes")
        print("  " + "─" * 100)
        for s in sources:
            imported = (s["last_imported"] or "")[:10]
            print(f"  {s['source']:<20} {s['latest_year'] or '?':>6}  {s['record_count']:>8}  {imported:<25}  {(s['notes'] or '')[:60]}")
    else:
        print("  No sources imported yet.")
        print()
        print("  Available sources to import:")
        for key, meta in SOURCE_META.items():
            verified = "MLS-verified" if meta["verified"] else "editorial"
            print(f"    • {key:<20} — {meta['display_name']} ({meta['cadence']}, {verified})")
            print(f"      {meta['url']}")


# ─────────────────────────────────────────────────────────────────────────────
# Generate agent-intel article for dashboard
# ─────────────────────────────────────────────────────────────────────────────

def generate_article(source, year, ranked_by="volume"):
    """
    Create a structured article entry in curation.db for the dashboard.
    Summarizes top Wasatch Back agents from a given source/year.
    """
    conn = get_conn()

    meta = SOURCE_META.get(source, {"display_name": source, "url": "", "verified": False})
    verified_note = "MLS-verified production data." if meta.get("verified") else "Editorial/self-reported data."

    # Pull Wasatch Back agents for this source/year
    wb_agents = conn.execute("""
        SELECT rank_position, agent_name, brokerage, city, volume_dollars, units
        FROM agent_rankings
        WHERE source=? AND year=? AND ranked_by=? AND wasatch_back=1
        ORDER BY rank_position ASC NULLS LAST
    """, (source, year, ranked_by)).fetchall()

    # Pull top 10 Utah statewide for context
    top_utah = conn.execute("""
        SELECT rank_position, agent_name, brokerage, city, volume_dollars, units
        FROM agent_rankings
        WHERE source=? AND year=? AND ranked_by=?
        ORDER BY rank_position ASC NULLS LAST
        LIMIT 10
    """, (source, year, ranked_by)).fetchall()

    # Build article body
    lines = [
        f"# {meta['display_name']} — Utah Rankings {year} (by {ranked_by.title()})",
        "",
        f"**Source:** {meta['display_name']}  |  **Year:** {year}  |  **Data:** {verified_note}",
        f"**URL:** {meta.get('url', '')}",
        "",
    ]

    if wb_agents:
        lines += [
            f"## Wasatch Back Agents ({len(wb_agents)} identified)",
            "",
        ]
        for a in wb_agents:
            vol = format_dollars(a["volume_dollars"])
            units_str = f"{a['units']} units" if a["units"] else ""
            lines.append(f"- **#{a['rank_position'] or '?'}** {a['agent_name']} — {a['brokerage'] or 'Unknown brokerage'}, {a['city']} | {vol} {units_str}")
        lines.append("")
    else:
        lines += [
            "## Wasatch Back Agents",
            "",
            "_No Wasatch Back agents found in this dataset. Check city name matching or expand WASATCH_BACK_CITIES list._",
            "",
        ]

    if top_utah:
        lines += [
            "## Top 10 Utah Statewide (context)",
            "",
        ]
        for a in top_utah:
            vol = format_dollars(a["volume_dollars"])
            wb_flag = " 🏔️" if is_wasatch_back(a["city"] or "") else ""
            lines.append(f"- **#{a['rank_position'] or '?'}** {a['agent_name']} — {a['brokerage'] or ''}, {a['city'] or '?'} | {vol}{wb_flag}")
        lines.append("")

    lines += [
        "---",
        f"_Generated by rankings_tracker.py from {meta['display_name']} {year} data. "
        f"Import date: {datetime.now().strftime('%Y-%m-%d')}_",
    ]

    body = "\n".join(lines)
    title = f"{meta['display_name']} Utah Rankings {year} — {len(wb_agents)} Wasatch Back Agents"
    uid = hashlib.sha1(f"rankings|{source}|{year}|{ranked_by}".encode()).hexdigest()
    now = datetime.now(timezone.utc).isoformat()

    # Insert into articles table (same schema as aggregator)
    try:
        conn.execute("""
            INSERT OR REPLACE INTO articles
                (uid, title, url, summary, published, source, category, market_area, status, fetched_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (
            uid, title,
            meta.get("url", ""),
            body[:500] + "..." if len(body) > 500 else body,
            f"{year}-01-01T00:00:00+00:00",
            meta["display_name"],
            "agent-intel",
            "Wasatch Back",
            "new",
            now,
        ))
        conn.commit()
        print(f"\n  ✓ Article created in dashboard: '{title}'")
        print(f"    Category: agent-intel | Market: Wasatch Back")
        print(f"    {len(wb_agents)} Wasatch Back agents summarized")
    except Exception as e:
        print(f"  ✗ Could not create article: {e}")
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Utah Mountain Living — Longitude Market Intelligence — Agent Performance Rankings Tracker"
    )
    parser.add_argument("--import",    dest="import_file", metavar="FILE",
                        help="Import rankings from CSV file")
    parser.add_argument("--source",    default="realtrends",
                        help="Source key (realtrends, uar, real_producers, summit_sir, manual, ...)")
    parser.add_argument("--year",      type=int, default=datetime.now().year - 1,
                        help="Rankings year (default: prior year)")
    parser.add_argument("--ranked-by", default="volume",
                        help="Metric used for ranking (volume, units, etc.)")
    parser.add_argument("--dry-run",   action="store_true",
                        help="Preview import without saving")
    parser.add_argument("--list",      action="store_true",
                        help="List stored rankings")
    parser.add_argument("--wasatch-back", action="store_true",
                        help="Show only Wasatch Back agents")
    parser.add_argument("--generate-article", action="store_true",
                        help="Generate dashboard article from stored rankings")
    parser.add_argument("--stats",     action="store_true",
                        help="Show database summary and available sources")
    parser.add_argument("--limit",     type=int, default=50,
                        help="Max rows to display (default: 50)")

    args = parser.parse_args()

    print()
    print("=" * 60)
    print("  Utah Mountain Living — Longitude Market Intelligence — Rankings Tracker")
    print("=" * 60)

    if args.import_file:
        n = import_csv(args.import_file, args.source, args.year,
                       ranked_by=args.ranked_by, dry_run=args.dry_run)
        if n > 0 and not args.dry_run:
            print()
            generate_article(args.source, args.year, args.ranked_by)

    elif args.wasatch_back:
        year = args.year if args.year else None
        show_wasatch_back(year=year)

    elif args.list:
        list_rankings(
            source=args.source if args.source != "realtrends" else None,
            year=args.year if args.year else None,
            limit=args.limit,
        )

    elif args.generate_article:
        generate_article(args.source, args.year, args.ranked_by)

    elif args.stats:
        show_stats()

    else:
        show_stats()

    print()


if __name__ == "__main__":
    main()
