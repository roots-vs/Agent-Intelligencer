#!/usr/bin/env python3
"""
movement_tracker.py — Utah Mountain Living — Longitude Market Intelligence Agent Movement Tracker
------------------------------------------------------------------
Checks known brokerage roster pages for new or removed agents and logs
changes as articles in curation.db with category='agent-intel'.

Each run:
  1. Fetches each roster page
  2. Extracts a normalized list of agent names
  3. Compares against the last stored snapshot
  4. Inserts a new article for each join or departure
  5. Saves a new snapshot

Usage:
    python movement_tracker.py                          # check all rosters
    python movement_tracker.py --dry-run                # print changes, don't save
    python movement_tracker.py --brokerage "Summit Sotheby's"  # check one brokerage

Requirements:
    Uses BeautifulSoup (bs4) if installed; falls back to regex name extraction.
    pip install beautifulsoup4  (optional but recommended)

Note: Brokerage roster pages change structure frequently. Review ROSTER_URLS
      and the extract_names() function after any changes.
"""

import sqlite3
import urllib.request
import urllib.error
import ssl
import json
import os
import sys
import argparse
import hashlib
import re
from datetime import datetime, timezone

DB_PATH    = os.path.join(os.path.dirname(__file__), "curation.db")
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

# Fallback SSL context
_SSL_UNVERIFIED = ssl.create_default_context()
_SSL_UNVERIFIED.check_hostname = False
_SSL_UNVERIFIED.verify_mode = ssl.CERT_NONE

# ─────────────────────────────────────────────────────────────────────────────
# ROSTER URLS
# Update these URLs as brokerage sites change.
# Each entry: (brokerage_name, roster_page_url)
# ─────────────────────────────────────────────────────────────────────────────
ROSTER_URLS = [
    ("Summit Sotheby's International Realty", "https://www.summitsir.com/agents/"),
    ("BHHS Utah Properties — Park City",      "https://utahrealestate.com/agent/search?city=Park+City"),
    ("Compass Park City",                     "https://www.compass.com/agents/park-city-ut/"),
    ("KW Park City — Keller Williams",        "https://www.kwparkcity.com/our-team/"),
    ("Engel & Volkers Park City",             "https://www.engelvoelkers.com/en-us/park-city/team/"),
    ("RE/MAX Park City",                      "https://www.remaxparkcity.com/agents/"),
    ("Coldwell Banker Park City",             "https://www.coldwellbanker.com/real-estate-agents/utah/park-city"),
]


# ─────────────────────────────────────────────────────────────────────────────
# Database setup
# ─────────────────────────────────────────────────────────────────────────────
def init_tables(conn):
    """Create roster_snapshots table and ensure articles table exists."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS roster_snapshots (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            brokerage   TEXT    NOT NULL,
            url         TEXT    NOT NULL,
            agent_names TEXT    NOT NULL,   -- JSON array of normalized names
            captured_at TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS articles (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            uid         TEXT    UNIQUE NOT NULL,
            source      TEXT    NOT NULL,
            category    TEXT    NOT NULL,
            market_area TEXT,
            title       TEXT    NOT NULL,
            link        TEXT    NOT NULL,
            summary     TEXT,
            published   TEXT,
            fetched_at  TEXT    NOT NULL,
            status      TEXT    DEFAULT 'new',
            issue_tag   TEXT,
            notes       TEXT
        );
    """)
    conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Fetch + parse
# ─────────────────────────────────────────────────────────────────────────────
def fetch_page(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        import ssl as _ssl
        ctx = _ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        with urllib.request.urlopen(req, timeout=20, context=_SSL_UNVERIFIED) as resp:
            return resp.read().decode("utf-8", errors="replace")


def extract_names_bs4(html, brokerage):
    """Use BeautifulSoup to extract agent names from common roster patterns."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    names = set()

    # Common patterns: h2/h3 with agent names, or elements with class containing 'agent', 'name', 'member'
    for tag in soup.find_all(["h2", "h3", "h4"]):
        text = tag.get_text(strip=True)
        if text and 2 < len(text) < 60 and not any(c.isdigit() for c in text[:3]):
            # Filter out nav/header text by checking for typical name structure
            parts = text.split()
            if 2 <= len(parts) <= 5:
                names.add(normalize_name(text))

    # Also try elements with agent-related class names
    for el in soup.find_all(class_=re.compile(r"agent|member|name|realtor", re.I)):
        text = el.get_text(strip=True)
        if text and 2 < len(text) < 60:
            parts = text.split()
            if 2 <= len(parts) <= 5:
                names.add(normalize_name(text))

    return sorted(n for n in names if n)


def extract_names_regex(html):
    """Fallback: extract likely agent names using regex patterns."""
    # Strip HTML tags
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)

    # Look for patterns like "First Last" in common roster contexts
    # This is approximate — review results and tune the regex for each site
    name_pattern = re.compile(
        r'\b([A-Z][a-z]{1,15}(?:\s+[A-Z][a-z]{0,2}\.?)?\s+[A-Z][a-z]{1,20})\b'
    )
    candidates = name_pattern.findall(text)

    # Filter noise
    stop_words = {"Real Estate", "Park City", "Salt Lake", "Heber City",
                  "View All", "Learn More", "Our Team", "Contact Us", "Sign Up"}
    names = set()
    for name in candidates:
        name = name.strip()
        if name and name not in stop_words and len(name.split()) >= 2:
            names.add(normalize_name(name))
    return sorted(names)


def normalize_name(name):
    """Normalize a name for consistent comparison."""
    name = re.sub(r"\s+", " ", name).strip()
    return name.title()


def extract_names(html, brokerage):
    """Extract agent names from HTML, using BeautifulSoup if available."""
    try:
        import bs4  # noqa: F401
        return extract_names_bs4(html, brokerage)
    except ImportError:
        return extract_names_regex(html)


# ─────────────────────────────────────────────────────────────────────────────
# Snapshot helpers
# ─────────────────────────────────────────────────────────────────────────────
def get_last_snapshot(conn, brokerage, url):
    row = conn.execute("""
        SELECT agent_names, captured_at FROM roster_snapshots
        WHERE brokerage = ? AND url = ?
        ORDER BY captured_at DESC LIMIT 1
    """, (brokerage, url)).fetchone()
    if row:
        return json.loads(row[0]), row[1]
    return None, None


def save_snapshot(conn, brokerage, url, names):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("""
        INSERT INTO roster_snapshots (brokerage, url, agent_names, captured_at)
        VALUES (?, ?, ?, ?)
    """, (brokerage, url, json.dumps(sorted(names)), now))
    conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Article insertion
# ─────────────────────────────────────────────────────────────────────────────
def insert_movement_article(conn, brokerage, agent_name, movement_type, url, dry_run=False):
    """
    Log an agent movement as an article in curation.db.
    movement_type: 'joined' or 'left'
    """
    now = datetime.now(timezone.utc).isoformat()

    if movement_type == "joined":
        title   = f"New agent at {brokerage}: {agent_name}"
        summary = (f"{agent_name} has joined {brokerage}. "
                   f"Detected by Utah Mountain Living — Longitude Market Intelligence roster monitor on {now[:10]}.")
    else:
        title   = f"{agent_name} left {brokerage}"
        summary = (f"{agent_name} no longer appears on the {brokerage} roster. "
                   f"Detected by Utah Mountain Living — Longitude Market Intelligence on {now[:10]}.")

    uid_str = hashlib.sha1(f"movement::{brokerage}::{agent_name}::{movement_type}::{now[:10]}".encode()).hexdigest()

    if dry_run:
        print(f"      {'✚' if movement_type == 'joined' else '✖'} {title}")
        return

    try:
        conn.execute("""
            INSERT OR IGNORE INTO articles
              (uid, source, category, market_area, title, link, summary, published, fetched_at)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (uid_str, brokerage, "agent-intel", "Wasatch Back",
              title, url, summary, now, now))
        conn.commit()
    except sqlite3.Error as e:
        print(f"      DB error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Main run
# ─────────────────────────────────────────────────────────────────────────────
def run(dry_run=False, brokerage_filter=None):
    conn = sqlite3.connect(DB_PATH)
    init_tables(conn)

    now = datetime.now(timezone.utc).isoformat()
    print(f"\n{'='*60}")
    print(f"  Utah Mountain Living — Longitude Market Intelligence — Agent Movement Tracker")
    print(f"  Run: {now[:19]}Z")
    if brokerage_filter:
        print(f"  Brokerage filter: {brokerage_filter}")
    if dry_run:
        print(f"  Mode: DRY RUN — no changes will be saved")
    print(f"{'='*60}\n")

    targets = ROSTER_URLS
    if brokerage_filter:
        targets = [(b, u) for b, u in ROSTER_URLS if brokerage_filter.lower() in b.lower()]
        if not targets:
            print(f"  No brokerage matched '{brokerage_filter}'")
            conn.close()
            return

    total_joins     = 0
    total_departures = 0

    for brokerage, url in targets:
        print(f"  Checking: {brokerage}")
        print(f"    URL: {url}")

        try:
            html  = fetch_page(url)
            names = extract_names(html, brokerage)

            if not names:
                print(f"    ⚠ No agent names extracted — roster page may have changed structure")
                print(f"      Review extract_names() for this site")
                continue

            print(f"    Found {len(names)} agent name(s) on current page")

            last_names, last_captured = get_last_snapshot(conn, brokerage, url)

            if last_names is None:
                print(f"    No previous snapshot — saving baseline ({len(names)} agents)")
                if not dry_run:
                    save_snapshot(conn, brokerage, url, names)
                continue

            last_set    = set(last_names)
            current_set = set(names)

            joined   = sorted(current_set - last_set)
            departed = sorted(last_set - current_set)

            if not joined and not departed:
                print(f"    ✓ No changes since {last_captured[:10]}")
            else:
                if joined:
                    print(f"    ✚ {len(joined)} new: {', '.join(joined[:5])}{'…' if len(joined) > 5 else ''}")
                    for name in joined:
                        insert_movement_article(conn, brokerage, name, "joined", url, dry_run)
                    total_joins += len(joined)
                if departed:
                    print(f"    ✖ {len(departed)} departed: {', '.join(departed[:5])}{'…' if len(departed) > 5 else ''}")
                    for name in departed:
                        insert_movement_article(conn, brokerage, name, "left", url, dry_run)
                    total_departures += len(departed)

                if not dry_run:
                    save_snapshot(conn, brokerage, url, names)

        except urllib.error.URLError as e:
            print(f"    ✗ Network error: {e.reason}")
        except Exception as e:
            print(f"    ✗ Error: {e}")

        print()

    print(f"{'='*60}")
    print(f"  Summary: {total_joins} joins, {total_departures} departures detected")
    print(f"{'='*60}\n")
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Utah Mountain Living — Longitude Market Intelligence Agent Movement Tracker")
    parser.add_argument("--dry-run",    action="store_true",
                        help="Print changes without saving to database")
    parser.add_argument("--brokerage",  type=str, default=None,
                        help="Only check rosters matching this brokerage name (partial match)")
    args = parser.parse_args()
    run(dry_run=args.dry_run, brokerage_filter=args.brokerage)
