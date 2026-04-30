#!/usr/bin/env python3
"""
agent_directory.py — Wasatch Intelligence Agent Directory
-----------------------------------------------------------
Maintains a local SQLite table of licensed real estate agents and brokers
in Summit County and Wasatch County, Utah.

Usage:
    python agent_directory.py --import agents.csv        # bulk import from CSV
    python agent_directory.py --search 'Smith'           # search by name/brokerage/office
    python agent_directory.py --export agents_export.csv # export full table to CSV
    python agent_directory.py --stats                    # show summary stats

Data Sources to populate from:
    1. Utah Division of Real Estate (realestate.utah.gov) — licensee lookup/export
    2. Brokerage roster pages (Summit Sotheby's, BHHS Utah, Compass Park City, etc.)
    3. Realtor.com / Zillow agent search by zip code (84060, 84098, 84032)

No external dependencies — Python standard library only.
"""

import sqlite3
import csv
import os
import sys
import argparse
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(__file__), "curation.db")


# ── Database setup ────────────────────────────────────────────────────────────
def init_agents_table(conn):
    """Create the agents table if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agents (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            name         TEXT    NOT NULL,
            brokerage    TEXT,
            office       TEXT,                    -- e.g. 'Park City', 'Heber City'
            county       TEXT,                    -- 'Summit' or 'Wasatch'
            email        TEXT,
            phone        TEXT,
            license_num  TEXT,
            license_type TEXT,                    -- 'Agent' or 'Broker'
            active       INTEGER DEFAULT 1,       -- 1 = active, 0 = inactive
            source       TEXT,                    -- where the record came from
            last_seen    TEXT,                    -- ISO date of last roster confirmation
            added_at     TEXT,
            notes        TEXT
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_agents_name ON agents(name)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_agents_brokerage ON agents(brokerage)
    """)
    conn.commit()


# ── Import ────────────────────────────────────────────────────────────────────
def import_csv(conn, filepath):
    """
    Bulk import agents from a CSV file.
    Expected columns (all optional except 'name'):
        name, brokerage, office, county, email, phone,
        license_num, license_type, active, source, last_seen, notes
    Existing records with the same license_num are updated; new records are inserted.
    """
    if not os.path.exists(filepath):
        print(f"  Error: file not found: {filepath}")
        sys.exit(1)

    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    updated  = 0

    with open(filepath, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = (row.get("name") or "").strip()
            if not name:
                continue

            license_num = (row.get("license_num") or "").strip() or None

            # Check for existing record by license number (most reliable key)
            existing_id = None
            if license_num:
                cur = conn.execute(
                    "SELECT id FROM agents WHERE license_num = ?", (license_num,)
                )
                existing = cur.fetchone()
                if existing:
                    existing_id = existing[0]

            fields = {
                "name":         name,
                "brokerage":    (row.get("brokerage")    or "").strip() or None,
                "office":       (row.get("office")       or "").strip() or None,
                "county":       (row.get("county")       or "").strip() or None,
                "email":        (row.get("email")        or "").strip() or None,
                "phone":        (row.get("phone")        or "").strip() or None,
                "license_num":  license_num,
                "license_type": (row.get("license_type") or "").strip() or None,
                "active":       int(row.get("active", 1) or 1),
                "source":       (row.get("source")       or "").strip() or None,
                "last_seen":    (row.get("last_seen")    or "").strip() or None,
                "notes":        (row.get("notes")        or "").strip() or None,
            }

            if existing_id:
                set_clause = ", ".join(f"{k} = ?" for k in fields)
                conn.execute(
                    f"UPDATE agents SET {set_clause} WHERE id = ?",
                    list(fields.values()) + [existing_id]
                )
                updated += 1
            else:
                fields["added_at"] = now
                cols   = ", ".join(fields.keys())
                places = ", ".join("?" for _ in fields)
                conn.execute(
                    f"INSERT INTO agents ({cols}) VALUES ({places})",
                    list(fields.values())
                )
                inserted += 1

    conn.commit()
    print(f"  Import complete: {inserted} inserted, {updated} updated")


# ── Search ────────────────────────────────────────────────────────────────────
def search(conn, query, show_inactive=False):
    """
    Full-text search across name, brokerage, and office.
    Returns and prints matching agents.
    """
    q = f"%{query}%"
    where = "WHERE (name LIKE ? OR brokerage LIKE ? OR office LIKE ?)"
    if not show_inactive:
        where += " AND active = 1"
    sql = f"""
        SELECT id, name, brokerage, office, county, license_type, email, phone,
               license_num, last_seen, source
        FROM agents
        {where}
        ORDER BY name
        LIMIT 50
    """
    rows = conn.execute(sql, (q, q, q)).fetchall()

    if not rows:
        print(f"  No agents found matching '{query}'")
        return []

    print(f"\n  {len(rows)} result(s) for '{query}':\n")
    print(f"  {'Name':<28} {'Brokerage':<32} {'Office':<14} {'Type':<8} {'License':<12}")
    print(f"  {'-'*28} {'-'*32} {'-'*14} {'-'*8} {'-'*12}")
    for r in rows:
        print(f"  {(r[1] or ''):<28} {(r[2] or ''):<32} {(r[3] or ''):<14} "
              f"{(r[5] or ''):<8} {(r[8] or ''):<12}")
    print()
    return rows


# ── Export ────────────────────────────────────────────────────────────────────
def export_csv(conn, filepath):
    """Export the full agents table to a CSV file."""
    rows = conn.execute("""
        SELECT id, name, brokerage, office, county, email, phone,
               license_num, license_type, active, source, last_seen, added_at, notes
        FROM agents
        ORDER BY name
    """).fetchall()

    if not rows:
        print("  No agents in database to export.")
        return

    headers = ["id", "name", "brokerage", "office", "county", "email", "phone",
               "license_num", "license_type", "active", "source", "last_seen",
               "added_at", "notes"]

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)

    print(f"  Exported {len(rows)} agents to {filepath}")


# ── Stats ─────────────────────────────────────────────────────────────────────
def show_stats(conn):
    """Print summary statistics about the agent directory."""
    total   = conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0]
    active  = conn.execute("SELECT COUNT(*) FROM agents WHERE active=1").fetchone()[0]
    summit  = conn.execute("SELECT COUNT(*) FROM agents WHERE county='Summit'").fetchone()[0]
    wasatch = conn.execute("SELECT COUNT(*) FROM agents WHERE county='Wasatch'").fetchone()[0]

    print(f"\n  Agent Directory Stats")
    print(f"  {'─'*40}")
    print(f"  Total agents:        {total}")
    print(f"  Active:              {active}")
    print(f"  Summit County:       {summit}")
    print(f"  Wasatch County:      {wasatch}")

    brokerages = conn.execute("""
        SELECT brokerage, COUNT(*) as n
        FROM agents WHERE active=1 AND brokerage IS NOT NULL
        GROUP BY brokerage ORDER BY n DESC LIMIT 10
    """).fetchall()

    if brokerages:
        print(f"\n  Top brokerages:")
        for b, n in brokerages:
            print(f"    {b:<38} {n}")
    print()


# ── CLI ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Wasatch Intelligence Agent Directory",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python agent_directory.py --import agents.csv
  python agent_directory.py --search 'Summit Sotheby'
  python agent_directory.py --search 'Park City' --include-inactive
  python agent_directory.py --export agents_export.csv
  python agent_directory.py --stats
        """
    )
    parser.add_argument("--import",           dest="import_file", metavar="FILE",
                        help="Import agents from a CSV file")
    parser.add_argument("--search",           dest="search_query", metavar="QUERY",
                        help="Search agents by name, brokerage, or office")
    parser.add_argument("--export",           dest="export_file", metavar="FILE",
                        help="Export all agents to a CSV file")
    parser.add_argument("--stats",            action="store_true",
                        help="Show summary statistics")
    parser.add_argument("--include-inactive", action="store_true",
                        help="Include inactive agents in search results")

    args = parser.parse_args()

    if not any([args.import_file, args.search_query, args.export_file, args.stats]):
        parser.print_help()
        sys.exit(0)

    conn = sqlite3.connect(DB_PATH)
    init_agents_table(conn)

    if args.import_file:
        import_csv(conn, args.import_file)

    if args.search_query:
        search(conn, args.search_query, show_inactive=args.include_inactive)

    if args.export_file:
        export_csv(conn, args.export_file)

    if args.stats:
        show_stats(conn)

    conn.close()


if __name__ == "__main__":
    main()
