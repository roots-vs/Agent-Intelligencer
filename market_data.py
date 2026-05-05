#!/usr/bin/env python3
"""
market_data.py — Utah Mountain Living — Longitude Market Intelligence Market Data Ingestion
------------------------------------------------------------
Fetches and stores Wasatch Back real estate market statistics.
Data is stored in the 'market_stats' table in curation.db.

Designed for manual CSV import from official sources such as:
  - Utah Association of Realtors (utahrealtors.com/news)
  - Park City Board of Realtors (pcbr.com)
  - Summit County Assessor / MLS summary reports

Usage:
    python market_data.py --import stats.csv       # bulk import from CSV
    python market_data.py --show 'Summit County'   # print latest stats for an area
    python market_data.py --show-all               # print latest stats for all areas
    python market_data.py --capsule 'Summit County' # generate GPT prompt capsule

CSV format (all columns required except 'notes'):
    area, metric, value, period, source, source_url, notes

  area:       e.g. 'Summit County', 'Wasatch County', 'Park City 84060'
  metric:     one of: median_sale_price | avg_sale_price | days_on_market |
                      active_listings | closed_sales | months_supply | list_to_sale_ratio
  value:      numeric (e.g. 1250000 for $1.25M, or 45 for 45 days)
  period:     e.g. '2026-Q1', '2026-03', '2025-annual'
  source:     e.g. 'Utah Association of Realtors'
  source_url: URL to the report or page
  notes:      optional free text

No external dependencies — Python standard library only.
"""

import sqlite3
import csv
import os
import sys
import argparse
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(__file__), "curation.db")

# Human-readable metric labels for display and GPT output
METRIC_LABELS = {
    "median_sale_price":    "Median Sale Price",
    "avg_sale_price":       "Average Sale Price",
    "days_on_market":       "Days on Market",
    "active_listings":      "Active Listings",
    "closed_sales":         "Closed Sales",
    "months_supply":        "Months of Supply",
    "list_to_sale_ratio":   "List-to-Sale Ratio (%)",
}


# ── Database setup ────────────────────────────────────────────────────────────
def init_market_stats_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_stats (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            area        TEXT    NOT NULL,   -- e.g. 'Summit County', 'Park City 84060'
            metric      TEXT    NOT NULL,   -- see METRIC_LABELS keys
            value       REAL    NOT NULL,
            period      TEXT    NOT NULL,   -- e.g. '2026-Q1', '2026-03'
            source      TEXT,
            source_url  TEXT,
            fetched_at  TEXT    NOT NULL,
            notes       TEXT
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_mstats_area ON market_stats(area)
    """)
    conn.commit()


# ── Import ────────────────────────────────────────────────────────────────────
def import_csv(conn, filepath):
    """
    Bulk import market stats from a CSV file.
    Duplicate area+metric+period combinations are replaced (most recent import wins).
    """
    if not os.path.exists(filepath):
        print(f"  Error: file not found: {filepath}")
        sys.exit(1)

    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    skipped  = 0

    with open(filepath, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            area   = (row.get("area")   or "").strip()
            metric = (row.get("metric") or "").strip()
            period = (row.get("period") or "").strip()

            if not area or not metric or not period:
                skipped += 1
                continue

            try:
                value = float(str(row.get("value", "")).replace(",", "").replace("$", "").strip())
            except ValueError:
                print(f"  Skipping row — non-numeric value: {row}")
                skipped += 1
                continue

            source     = (row.get("source")     or "").strip() or None
            source_url = (row.get("source_url") or "").strip() or None
            notes      = (row.get("notes")      or "").strip() or None

            # Delete existing record for same area+metric+period so we keep latest
            conn.execute("""
                DELETE FROM market_stats WHERE area=? AND metric=? AND period=?
            """, (area, metric, period))

            conn.execute("""
                INSERT INTO market_stats (area, metric, value, period, source, source_url, fetched_at, notes)
                VALUES (?,?,?,?,?,?,?,?)
            """, (area, metric, value, period, source, source_url, now, notes))
            inserted += 1

    conn.commit()
    print(f"  Import complete: {inserted} records inserted/updated, {skipped} skipped")


# ── Display helpers ───────────────────────────────────────────────────────────
def format_value(metric, value):
    """Format a numeric value for display based on metric type."""
    if "price" in metric:
        if value >= 1_000_000:
            return f"${value/1_000_000:.2f}M"
        return f"${value:,.0f}"
    if "ratio" in metric:
        return f"{value:.1f}%"
    if "days" in metric:
        return f"{value:.0f} days"
    if "months" in metric:
        return f"{value:.1f} mo."
    return f"{value:,.0f}"


def show_latest(conn, area, top_n=1):
    """Print the most recent stats for a given area."""
    rows = conn.execute("""
        SELECT metric, value, period, source, source_url, fetched_at, notes
        FROM market_stats
        WHERE area = ?
        ORDER BY fetched_at DESC
        LIMIT ?
    """, (area, top_n * len(METRIC_LABELS))).fetchall()

    if not rows:
        print(f"  No data found for area: '{area}'")
        print(f"  Run --show-all to see available areas.")
        return

    # Group by period, show most recent period first
    by_period = {}
    for r in rows:
        p = r[2]
        if p not in by_period:
            by_period[p] = []
        by_period[p].append(r)

    print(f"\n  Market Stats — {area}")
    print(f"  {'─'*50}")
    for period, records in sorted(by_period.items(), reverse=True)[:top_n]:
        print(f"\n  Period: {period}")
        for metric, value, _, source, source_url, fetched_at, notes in records:
            label     = METRIC_LABELS.get(metric, metric)
            formatted = format_value(metric, value)
            src_str   = f"  [{source}]" if source else ""
            print(f"    {label:<28} {formatted:<14}{src_str}")
        if records and records[0][3]:
            print(f"\n  Source: {records[0][3]}")
            if records[0][4]:
                print(f"  URL:    {records[0][4]}")
    print()


def show_all(conn):
    """Show the most recent entry for each area."""
    areas = conn.execute("""
        SELECT DISTINCT area FROM market_stats ORDER BY area
    """).fetchall()

    if not areas:
        print("  No market data in database. Run --import stats.csv to add data.")
        return

    for (area,) in areas:
        show_latest(conn, area, top_n=1)


# ── GPT Capsule ───────────────────────────────────────────────────────────────
def generate_capsule(conn, area):
    """
    Generate a text capsule of the latest market stats for use in a GPT prompt.
    This is inserted into the Compose panel's prompt output.
    """
    rows = conn.execute("""
        SELECT metric, value, period, source
        FROM market_stats
        WHERE area = ?
        ORDER BY fetched_at DESC
        LIMIT ?
    """, (area, len(METRIC_LABELS))).fetchall()

    if not rows:
        return f"[No market data available for {area}]"

    # Use most recent period
    period = rows[0][2]
    source = rows[0][3] or "unknown source"

    lines = [f"MARKET DATA CAPSULE — {area} ({period})", f"Source: {source}", ""]
    for metric, value, p, _ in rows:
        if p == period:
            label     = METRIC_LABELS.get(metric, metric)
            formatted = format_value(metric, value)
            lines.append(f"  {label}: {formatted}")

    lines += [
        "",
        "FACT BOUNDARY: Use only the numbers above when citing market conditions.",
        "Do not assert trends or comparisons unless additional periods are provided below.",
    ]

    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Utah Mountain Living — Longitude Market Intelligence Market Data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python market_data.py --import stats.csv
  python market_data.py --show 'Summit County'
  python market_data.py --show 'Park City 84060'
  python market_data.py --show-all
  python market_data.py --capsule 'Summit County'

CSV column headers:
  area, metric, value, period, source, source_url, notes

Metric values:
  median_sale_price | avg_sale_price | days_on_market |
  active_listings | closed_sales | months_supply | list_to_sale_ratio
        """
    )
    parser.add_argument("--import",   dest="import_file", metavar="FILE",
                        help="Import market stats from a CSV file")
    parser.add_argument("--show",     dest="show_area",   metavar="AREA",
                        help="Show latest stats for an area")
    parser.add_argument("--show-all", action="store_true",
                        help="Show latest stats for all areas")
    parser.add_argument("--capsule",  dest="capsule_area", metavar="AREA",
                        help="Generate a GPT prompt capsule for an area")

    args = parser.parse_args()

    if not any([args.import_file, args.show_area, args.show_all, args.capsule_area]):
        parser.print_help()
        sys.exit(0)

    conn = sqlite3.connect(DB_PATH)
    init_market_stats_table(conn)

    if args.import_file:
        import_csv(conn, args.import_file)

    if args.show_area:
        show_latest(conn, args.show_area)

    if args.show_all:
        show_all(conn)

    if args.capsule_area:
        capsule = generate_capsule(conn, args.capsule_area)
        print("\n" + capsule + "\n")

    conn.close()


if __name__ == "__main__":
    main()
