#!/usr/bin/env python3
"""
aggregator.py — Wasatch Intelligence Content Aggregator
---------------------------------------------------------
Fetches all RSS/Atom feeds in feeds.py and stores new articles to SQLite.
Run this daily (or manually) to populate the curation database.

Usage:
    python aggregator.py                              # fetch all feeds
    python aggregator.py --days 3                     # only keep articles from last N days
    python aggregator.py --dry-run                    # print what would be fetched, don't save
    python aggregator.py --category local-market      # fetch only feeds in one category
"""

import sqlite3
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
import hashlib
import argparse
import sys
import os
import ssl
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

try:
    import certifi
    _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CONTEXT = ssl.create_default_context()

# Fallback for feeds with expired/self-signed certs
_SSL_UNVERIFIED = ssl.create_default_context()
_SSL_UNVERIFIED.check_hostname = False
_SSL_UNVERIFIED.verify_mode = ssl.CERT_NONE

# ── Config ────────────────────────────────────────────────────────────────────
DB_PATH    = os.path.join(os.path.dirname(__file__), "curation.db")
MAX_DAYS   = 90        # drop articles older than this (keeps ~3 months of history)
MAX_ITEMS  = 50        # max items to pull per feed per run
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

# ── market_area mapping by category ──────────────────────────────────────────
MARKET_AREA_MAP = {
    "local-market":        "Wasatch Back",
    "agent-intel":         "Wasatch Back",
    "market-data":         "Wasatch Back",
    "national-brokerage":  "National",
    "industry-practice":   "National",
    "comp-market":         "Comp Market",
}

# ── Namespaces for Atom / Dublin Core ────────────────────────────────────────
NS = {
    "atom":    "http://www.w3.org/2005/Atom",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc":      "http://purl.org/dc/elements/1.1/",
    "media":   "http://search.yahoo.com/mrss/",
}


# ── Database setup ────────────────────────────────────────────────────────────
def init_db(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS articles (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            uid         TEXT    UNIQUE NOT NULL,   -- hash of feed+link
            source      TEXT    NOT NULL,
            category    TEXT    NOT NULL,
            market_area TEXT,                      -- 'Wasatch Back' | 'National' | 'Comp Market'
            title       TEXT    NOT NULL,
            link        TEXT    NOT NULL,
            summary     TEXT,
            published   TEXT,                      -- ISO datetime string
            fetched_at  TEXT    NOT NULL,
            status      TEXT    DEFAULT 'new',     -- new | reviewed | selected | skip | archived
            issue_tag   TEXT,                      -- e.g. "April 2026 Market Update"
            notes       TEXT
        );

        CREATE TABLE IF NOT EXISTS fetch_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            source      TEXT,
            run_at      TEXT,
            items_added INTEGER,
            error       TEXT
        );
    """)
    conn.commit()


# ── RSS/Atom parsing ─────────────────────────────────────────────────────────
def fetch_feed(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15, context=_SSL_CONTEXT) as resp:
            return resp.read()
    except ssl.SSLCertVerificationError:
        # Retry without cert verification for feeds with expired/self-signed certs
        with urllib.request.urlopen(req, timeout=15, context=_SSL_UNVERIFIED) as resp:
            return resp.read()


import re as _re
_INVALID_XML_CHARS = _re.compile(
    r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F'
    r'\uD800-\uDFFF\uFFFE\uFFFF]'
)

def sanitize_xml(xml_bytes):
    """
    Multi-pass cleaner for malformed real-world RSS feeds.
    Handles:
      1. Illegal XML 1.0 characters (control chars, etc.)
      2. Junk content after the closing </rss> or </feed> tag
      3. Unescaped & characters in text/URLs (common in WP feeds)
    """
    try:
        text = xml_bytes.decode("utf-8", errors="replace")
    except Exception:
        text = xml_bytes.decode("latin-1", errors="replace")

    # 1. Strip illegal XML control characters
    text = _INVALID_XML_CHARS.sub("", text)

    # 2. Truncate anything after the closing root element
    for closing_tag in ("</rss>", "</feed>", "</rdf:RDF>"):
        idx = text.lower().rfind(closing_tag.lower())
        if idx != -1:
            text = text[: idx + len(closing_tag)]
            break

    # 3. Fix unescaped & that aren't already part of an entity reference
    import re as _re
    text = _re.sub(r"&(?!(?:[a-zA-Z][a-zA-Z0-9]*|#[0-9]+|#x[0-9a-fA-F]+);)", "&amp;", text)

    return text.encode("utf-8")


def parse_date(date_str):
    """Try to parse various date formats into an ISO string."""
    if not date_str:
        return None
    try:
        dt = parsedate_to_datetime(date_str)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            return dt.replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            continue
    return date_str  # return raw if nothing parses


def text_or_none(el):
    if el is None:
        return None
    return (el.text or "").strip() or None


def strip_html(text):
    """Very light HTML tag stripper for summaries."""
    if not text:
        return text
    import re
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:600] + ("…" if len(text) > 600 else "")


def uid(source, link):
    return hashlib.sha1(f"{source}::{link}".encode()).hexdigest()


def parse_rss(xml_bytes, source, category):
    """Parse RSS 2.0 feed, return list of article dicts."""
    root = ET.fromstring(xml_bytes)
    channel = root.find("channel")
    if channel is None:
        return []
    articles = []
    for item in channel.findall("item")[:MAX_ITEMS]:
        title   = text_or_none(item.find("title"))
        link    = text_or_none(item.find("link"))
        pub     = text_or_none(item.find("pubDate"))
        summary = (
            text_or_none(item.find("description")) or
            text_or_none(item.find("{%s}encoded" % NS["content"]))
        )
        if not link:
            continue
        articles.append({
            "source":      source,
            "category":    category,
            "market_area": MARKET_AREA_MAP.get(category, "National"),
            "title":       title or "(no title)",
            "link":        link,
            "summary":     strip_html(summary),
            "published":   parse_date(pub),
            "uid":         uid(source, link),
        })
    return articles


def parse_atom(xml_bytes, source, category):
    """Parse Atom feed, return list of article dicts."""
    root = ET.fromstring(xml_bytes)
    tag = root.tag
    ns_prefix = ""
    if tag.startswith("{"):
        ns_prefix = tag[1:tag.index("}")]

    def find(el, tag_name):
        return el.find(f"{{{ns_prefix}}}{tag_name}" if ns_prefix else tag_name)

    articles = []
    entries = root.findall(f"{{{ns_prefix}}}entry" if ns_prefix else "entry")
    for entry in entries[:MAX_ITEMS]:
        title_el = find(entry, "title")
        title    = text_or_none(title_el)
        link = None
        for l in entry.findall(f"{{{ns_prefix}}}link" if ns_prefix else "link"):
            rel  = l.get("rel", "alternate")
            href = l.get("href")
            if href and rel in ("alternate", ""):
                link = href
                break
        if not link:
            continue
        pub = (
            text_or_none(find(entry, "updated")) or
            text_or_none(find(entry, "published"))
        )
        summary_el = find(entry, "summary") or find(entry, "content")
        summary    = text_or_none(summary_el)
        articles.append({
            "source":      source,
            "category":    category,
            "market_area": MARKET_AREA_MAP.get(category, "National"),
            "title":       title or "(no title)",
            "link":        link,
            "summary":     strip_html(summary),
            "published":   parse_date(pub),
            "uid":         uid(source, link),
        })
    return articles


def detect_and_parse(xml_bytes, source, category):
    """Auto-detect RSS vs Atom and parse. Sanitizes invalid XML chars on first failure."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        xml_bytes = sanitize_xml(xml_bytes)
        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError as e:
            raise ValueError(f"XML parse error: {e}")
    tag = root.tag.lower()
    if "feed" in tag:
        return parse_atom(xml_bytes, source, category)
    else:
        return parse_rss(xml_bytes, source, category)


# ── Age filter ────────────────────────────────────────────────────────────────
def too_old(pub_str, max_days):
    if not pub_str:
        return False
    try:
        pub_dt = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_days)
        return pub_dt < cutoff
    except Exception:
        return False


# ── Main run ──────────────────────────────────────────────────────────────────
def run(dry_run=False, max_days=MAX_DAYS, category_filter=None):
    from feeds import FEEDS, NO_RSS_SOURCES as EMAIL_ONLY_SOURCES

    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    total_added = 0
    now = datetime.now(timezone.utc).isoformat()

    print(f"\n{'='*60}")
    print(f"  Wasatch Intelligence Content Aggregator")
    print(f"  Run: {now[:19]}Z")
    if category_filter:
        print(f"  Category filter: {category_filter}")
    print(f"{'='*60}\n")

    # ── Build feed list ──
    all_feeds = list(FEEDS)
    # Add any Kill the Newsletter feeds from NO_RSS_SOURCES
    for src in EMAIL_ONLY_SOURCES:
        ktn = src.get("kill_the_newsletter_feed", "").strip()
        if ktn:
            all_feeds.append((src["name"], ktn, "industry-practice", src.get("notes", "")))
        gen = src.get("generated_feed", "").strip()
        if gen and not ktn:
            # rss.app generated feeds — use original category if available, default to national-brokerage
            cat = src.get("category", "national-brokerage")
            all_feeds.append((src["name"], gen, cat, src.get("notes", "")))

    # Apply --category filter
    if category_filter:
        all_feeds = [(l, u, c, n) for l, u, c, n in all_feeds if c == category_filter]
        if not all_feeds:
            print(f"  No feeds found for category '{category_filter}'.")
            conn.close()
            return

    for label, url, category, notes in all_feeds:
        print(f"  [{category}] {label}")
        try:
            xml_bytes = fetch_feed(url)
            articles  = detect_and_parse(xml_bytes, label, category)
            added = 0
            for art in articles:
                if too_old(art["published"], max_days):
                    continue
                if dry_run:
                    print(f"      → {art['title'][:70]}")
                    added += 1
                    continue
                try:
                    conn.execute("""
                        INSERT OR IGNORE INTO articles
                          (uid, source, category, market_area, title, link, summary, published, fetched_at)
                        VALUES (?,?,?,?,?,?,?,?,?)
                    """, (
                        art["uid"], art["source"], art["category"], art["market_area"],
                        art["title"], art["link"], art["summary"],
                        art["published"], now
                    ))
                    if conn.execute("SELECT changes()").fetchone()[0] > 0:
                        added += 1
                except sqlite3.Error as e:
                    print(f"      DB error: {e}")
            conn.commit()
            print(f"      ✓ {len(articles)} fetched, {added} new added")
            conn.execute("INSERT INTO fetch_log (source,run_at,items_added) VALUES (?,?,?)",
                         (label, now, added))
            total_added += added
        except urllib.error.URLError as e:
            msg = f"Network error: {e.reason}"
            print(f"      ✗ {msg}")
            conn.execute("INSERT INTO fetch_log (source,run_at,items_added,error) VALUES (?,?,?,?)",
                         (label, now, 0, msg))
        except Exception as e:
            msg = str(e)
            print(f"      ✗ {msg}")
            conn.execute("INSERT INTO fetch_log (source,run_at,items_added,error) VALUES (?,?,?,?)",
                         (label, now, 0, msg))
        conn.commit()

    print(f"\n{'='*60}")
    print(f"  Total new articles added: {total_added}")
    print(f"{'='*60}\n")
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Wasatch Intelligence RSS Aggregator")
    parser.add_argument("--days",     type=int, default=MAX_DAYS,
                        help="Max article age in days (default: 90)")
    parser.add_argument("--dry-run",  action="store_true",
                        help="Print results without saving to database")
    parser.add_argument("--category", type=str, default=None,
                        help="Only fetch feeds in this category (e.g. local-market, agent-intel)")
    args = parser.parse_args()
    run(dry_run=args.dry_run, max_days=args.days, category_filter=args.category)
