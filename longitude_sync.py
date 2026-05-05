#!/usr/bin/env python3
"""
longitude_sync.py — Longitude Network ↔ Agent Intelligencer Integration
------------------------------------------------------------------------
Connects the Longitude Network PostgreSQL database (3,669 agents) to the
local Agent Intelligencer SQLite curation database.

What it does:
  1. Agent Trigger Detection  — scans RSS articles for agent name mentions
                                and logs them as agent-intel signals
  2. Performance Snapshots    — generates agent-intel articles from
                                realtrends_rank and zillow_sales_12mo data
  3. Network Stats Article    — posts a market-data summary of Wasatch Back
                                agent distribution across brokerages
  4. article_agent_links      — join table linking articles ↔ agents for
                                digest generation

Usage:
    python3 longitude_sync.py                        # full sync (all modes)
    python3 longitude_sync.py --scan-articles        # name-match only
    python3 longitude_sync.py --performance-snapshot # rankings articles only
    python3 longitude_sync.py --network-stats        # brokerage distribution
    python3 longitude_sync.py --dry-run              # print without saving
    python3 longitude_sync.py --stats                # show link counts
    python3 longitude_sync.py --agent "Jane Smith"   # check one agent

PostgreSQL connection:
    host: localhost  port: 5433  db: longitude_network
    user: longitude  password: longitude_dev_2024

Override with env vars:
    PG_HOST, PG_PORT, PG_DBNAME, PG_USER, PG_PASSWORD
"""

import sqlite3
import hashlib
import argparse
import os
import re
import sys
from datetime import datetime, timezone
from difflib import SequenceMatcher

# ── Config ────────────────────────────────────────────────────────────────────
SQLITE_DB  = os.path.expanduser("~/.longitude/curation.db")

PG_CONFIG = {
    "host":     os.environ.get("PG_HOST",     "localhost"),
    "port":     int(os.environ.get("PG_PORT", "5433")),
    "dbname":   os.environ.get("PG_DBNAME",   "longitude_network"),
    "user":     os.environ.get("PG_USER",     "longitude"),
    "password": os.environ.get("PG_PASSWORD", "longitude_dev_2024"),
}

# Name-match thresholds
EXACT_MATCH_SCORE    = 1.0
FUZZY_MATCH_CUTOFF   = 0.82   # SequenceMatcher ratio — tune up to reduce false positives
MIN_NAME_LENGTH      = 5      # skip names shorter than this (common words)

# Wasatch Back brokerage filters for performance snapshot
WASATCH_BACK_BROKERAGES = {
    "summit sotheby", "sotheby", "kw park city", "keller williams park city",
    "compass park city", "compass", "bhhs utah", "berkshire hathaway",
    "engel", "volkers", "engel & volkers", "coldwell banker", "remax",
    "re/max", "windermere", "christies", "christie's", "utah real estate",
    "park city real estate", "wasatch", "heber", "midway",
}

SOURCE_LABEL = "Longitude Network"

# ── Topic intelligence triggers ───────────────────────────────────────────────
# Each topic cluster: (label, category, market_area, keywords)
# An article matching ANY keyword in the list gets tagged to that cluster.
TOPIC_CLUSTERS = [
    (
        "Wasatch Back Development",
        "agent-intel", "Wasatch Back",
        [
            "deer valley", "deer valley east", "canyons village", "park city",
            "heber valley", "midway", "sundance", "talisker", "empire pass",
            "promontory", "victory ranch", "extell", "extell development",
            "wasatch county", "summit county", "snyderville",
        ]
    ),
    (
        "Market Conditions & Interest Rates",
        "agent-intel", "National",
        [
            "interest rate", "fed rate", "federal reserve", "mortgage rate",
            "30-year", "rate cut", "rate hike", "inflation", "housing supply",
            "inventory", "months of supply", "days on market", "median price",
            "price reduction", "home price", "affordability",
        ]
    ),
    (
        "AI & Technology for Agents",
        "agent-intel", "National",
        [
            "artificial intelligence", " ai ", "ai-powered", "ai tool",
            "chatgpt", "generative ai", "machine learning", "proptech",
            "listing description", "virtual tour", "automated valuation",
            "avm", "lead generation", "crm", "mls technology", "real estate tech",
        ]
    ),
    (
        "Luxury & Resort Market Signals",
        "agent-intel", "Wasatch Back",
        [
            "luxury", "ultra-luxury", "ski-in", "ski-out", "ski resort",
            "mountain resort", "resort community", "second home", "vacation home",
            "1031 exchange", "high net worth", "ultra high net worth",
            "private equity", "family office",
        ]
    ),
    (
        "Brokerage & Industry Moves",
        "agent-intel", "National",
        [
            "brokerage", "merger", "acquisition", "expansion", "franchise",
            "independent brokerage", "team formation", "team leader",
            "agent exodus", "agent recruitment", "splits", "commission",
            "nar settlement", "buyer agreement", "buyer representation",
            "cooperative compensation",
        ]
    ),
    (
        "Selling Strategy & Best Practices",
        "agent-intel", "National",
        [
            "listing presentation", "open house", "staging", "photography",
            "drone", "video tour", "days on market", "price strategy",
            "negotiation", "multiple offers", "offer review", "contingency",
            "seller concession", "buyer incentive", "marketing plan",
        ]
    ),
]


# ── Utilities ─────────────────────────────────────────────────────────────────
def uid(*parts):
    """SHA1 uid — same scheme as aggregator.py."""
    key = "::".join(str(p) for p in parts)
    return hashlib.sha1(key.encode()).hexdigest()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def pg_connect():
    """Connect to PostgreSQL. Raises ImportError if psycopg2 not installed."""
    try:
        import psycopg2
        import psycopg2.extras
    except ImportError:
        print("  ✗  psycopg2 not found. Install with:")
        print("       pip install psycopg2-binary --break-system-packages")
        sys.exit(1)
    return psycopg2.connect(**PG_CONFIG)


# ── SQLite schema extensions ──────────────────────────────────────────────────
def init_sqlite_extensions(conn):
    """Add tables needed for the integration (safe to run multiple times)."""
    conn.executescript("""
        -- Link table: which agents are mentioned in which articles
        CREATE TABLE IF NOT EXISTS article_agent_links (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            article_uid TEXT    NOT NULL,
            agent_email TEXT    NOT NULL,
            agent_name  TEXT    NOT NULL,
            match_type  TEXT    NOT NULL,   -- 'exact' | 'fuzzy' | 'manual'
            match_score REAL    DEFAULT 1.0,
            created_at  TEXT    NOT NULL,
            UNIQUE(article_uid, agent_email)
        );

        -- Cache of agents pulled from PostgreSQL (refreshed each sync run)
        CREATE TABLE IF NOT EXISTS longitude_agents (
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
        );
    """)
    conn.commit()


# ── Load agents from PostgreSQL ───────────────────────────────────────────────
def load_agents_from_pg():
    """Pull all agents from PostgreSQL agents table. Returns list of dicts."""
    pg = pg_connect()
    cur = pg.cursor()

    # Fetch columns we care about — gracefully handle missing columns
    cur.execute("""
        SELECT
            COALESCE(full_name, '')            AS full_name,
            COALESCE(email, '')                AS email,
            COALESCE(brokerage_name, '')       AS brokerage_name,
            COALESCE(specialty, '')            AS specialty,
            zillow_sales_12mo,
            zillow_price_range_max,
            realtrends_rank,
            COALESCE(ig_handle, '')            AS ig_handle,
            COALESCE(agent_website, '')        AS agent_website,
            COALESCE(zillow_service_areas::text, '') AS zillow_service_areas
        FROM agents
        WHERE full_name IS NOT NULL AND full_name != ''
        ORDER BY full_name
    """)
    cols = [d[0] for d in cur.description]
    agents = [dict(zip(cols, row)) for row in cur.fetchall()]
    pg.close()
    print(f"  ✓  Loaded {len(agents):,} agents from PostgreSQL")
    return agents


def cache_agents_in_sqlite(sqlite_conn, agents):
    """Write agent list into longitude_agents table for offline use."""
    now = now_iso()
    sqlite_conn.execute("DELETE FROM longitude_agents")
    sqlite_conn.executemany("""
        INSERT OR REPLACE INTO longitude_agents
          (email, full_name, brokerage_name, specialty,
           zillow_sales_12mo, zillow_price_range_max, realtrends_rank,
           ig_handle, agent_website, zillow_service_areas, last_synced)
        VALUES (:email, :full_name, :brokerage_name, :specialty,
                :zillow_sales_12mo, :zillow_price_range_max, :realtrends_rank,
                :ig_handle, :agent_website, :zillow_service_areas, :last_synced)
    """, [{**a, "last_synced": now} for a in agents])
    sqlite_conn.commit()
    print(f"  ✓  Cached {len(agents):,} agents in longitude_agents table")


# ── Agent name matching ───────────────────────────────────────────────────────
def build_name_index(agents):
    """
    Build a lookup structure for name matching.
    Only indexes agents with at least first + last name (2+ words).
    Returns dict: normalized_name → agent dict
    """
    index = {}
    for a in agents:
        name = a["full_name"].strip()
        parts = name.split()
        if len(parts) < 2:          # require first + last
            continue
        if len(name) < MIN_NAME_LENGTH:
            continue
        key = name.lower()
        index[key] = a
    return index


def find_agents_in_text(text_lower, name_index):
    """
    Find agents where first AND last name both appear as whole words
    within 5 word-positions of each other in the text.

    No fuzzy matching — requires exact word presence.
    Match type is 'exact' when the full name appears as a contiguous phrase
    (in name order), 'proximity' when within the 5-word window but non-adjacent.
    """
    # Build word-position index for the text once, shared across all agents
    words = re.findall(r'\b[a-z]+\b', text_lower)
    positions: dict = {}
    for i, w in enumerate(words):
        positions.setdefault(w, []).append(i)

    matches = []
    seen = set()

    for norm_name, agent in name_index.items():
        if agent["email"] in seen:
            continue

        parts      = norm_name.split()
        first_word = parts[0]
        last_word  = parts[-1]

        first_pos = positions.get(first_word, [])
        last_pos  = positions.get(last_word, [])

        if not first_pos or not last_pos:
            continue

        found    = False
        is_exact = False
        for fp in first_pos:
            for lp in last_pos:
                if abs(fp - lp) <= 5:
                    found = True
                    # Exact: words appear in name order with no extra words between
                    if lp - fp == len(parts) - 1:
                        is_exact = True
                    break
            if found:
                break

        if found:
            match_type = "exact" if is_exact else "proximity"
            score      = 1.0 if is_exact else 0.9
            matches.append((agent, match_type, score))
            seen.add(agent["email"])

    return matches


# ── Article scanning ──────────────────────────────────────────────────────────
def scan_articles_for_agents(sqlite_conn, agents, dry_run=False, days_back=90):
    """
    Scan recent articles for agent name mentions.
    Inserts rows into article_agent_links.
    Returns count of new links created.
    """
    name_index = build_name_index(agents)
    now = now_iso()

    cur = sqlite_conn.execute("""
        SELECT uid, title, summary, source, category, published, link
        FROM articles
        WHERE fetched_at >= datetime('now', ?)
        ORDER BY fetched_at DESC
    """, (f"-{days_back} days",))
    articles = cur.fetchall()

    print(f"\n  Scanning {len(articles):,} articles for agent mentions...")

    links_created = 0
    agent_hits = {}  # email → [article titles]

    for uid_, title, summary, source, category, published, link in articles:
        text = f"{title or ''} {summary or ''}".lower()

        matches = find_agents_in_text(text, name_index)  # no fuzzy — proximity only
        for agent, match_type, score in matches:
            email = agent["email"]
            if dry_run:
                agent_hits.setdefault(email, []).append(title[:60])
                links_created += 1
                continue
            try:
                sqlite_conn.execute("""
                    INSERT OR IGNORE INTO article_agent_links
                      (article_uid, agent_email, agent_name, match_type, match_score, created_at)
                    VALUES (?,?,?,?,?,?)
                """, (uid_, email, agent["full_name"], match_type, score, now))
                if sqlite_conn.execute("SELECT changes()").fetchone()[0] > 0:
                    links_created += 1
                    agent_hits.setdefault(email, []).append(title[:60])
            except sqlite3.Error as e:
                print(f"    DB error: {e}")

    if not dry_run:
        sqlite_conn.commit()

    # Print summary of matches found
    if agent_hits:
        print(f"\n  Agent mentions found ({len(agent_hits)} agents, {links_created} links):")
        for email, titles in sorted(agent_hits.items())[:20]:
            name = next((a["full_name"] for a in agents if a["email"] == email), email)
            print(f"    • {name}: {len(titles)} article(s)")
            for t in titles[:2]:
                print(f"        – {t}")
        if len(agent_hits) > 20:
            print(f"    ... and {len(agent_hits) - 20} more agents")
    else:
        print("    No agent name mentions found in recent articles.")
        print("    (This is normal if article sources don't name individual agents.)")
        print("    Use --performance-snapshot to generate articles from ranking data instead.")

    return links_created


# ── Topic-based relevance scanning ───────────────────────────────────────────
def scan_articles_for_topics(sqlite_conn, dry_run=False, days_back=90):
    """
    Scan recent articles for topic cluster keyword matches.
    Tags matched articles with a topic label in the notes field so they
    surface in the Agent Intel tab.

    An article is tagged if it matches ANY keyword in a topic cluster.
    High-signal articles (matching Wasatch Back clusters) are also
    re-categorized to agent-intel so they appear in that dashboard tab.
    """
    now = now_iso()
    tagged = 0
    cluster_counts = {}

    cur = sqlite_conn.execute("""
        SELECT uid, title, summary, source, category, market_area, published, link
        FROM articles
        WHERE fetched_at >= datetime('now', ?)
        ORDER BY fetched_at DESC
    """, (f"-{days_back} days",))
    articles = cur.fetchall()

    print(f"\n  Scanning {len(articles):,} articles for topic signals…")

    for uid_, title, summary, source, category, market_area, published, link in articles:
        text = f"{title or ''} {summary or ''}".lower()

        for cluster_label, cluster_cat, cluster_market, keywords in TOPIC_CLUSTERS:
            hit = next((kw for kw in keywords if kw in text), None)
            if not hit:
                continue

            cluster_counts[cluster_label] = cluster_counts.get(cluster_label, 0) + 1

            if dry_run:
                tagged += 1
                continue

            # Tag the article — append topic label to notes, promote to agent-intel
            # if it's currently local-market or industry-practice and matches a
            # Wasatch Back or high-signal cluster
            promote = cluster_market == "Wasatch Back" or cluster_label in (
                "Market Conditions & Interest Rates",
                "AI & Technology for Agents",
                "Brokerage & Industry Moves",
            )

            if promote and category in ("local-market", "industry-practice", "national-brokerage", "comp-market"):
                sqlite_conn.execute("""
                    UPDATE articles
                    SET notes = CASE
                            WHEN notes IS NULL THEN ?
                            WHEN notes NOT LIKE ? THEN notes || ' | ' || ?
                            ELSE notes
                        END,
                        category = CASE
                            WHEN ? = 1 AND category != 'market-data' THEN 'agent-intel'
                            ELSE category
                        END
                    WHERE uid = ?
                """, (
                    f"topic:{cluster_label}",
                    f"%{cluster_label}%",
                    f"topic:{cluster_label}",
                    1 if promote else 0,
                    uid_,
                ))
                tagged += 1

    if not dry_run:
        sqlite_conn.commit()

    if cluster_counts:
        print(f"\n  Topic signals detected ({tagged} articles tagged):")
        for label, count in sorted(cluster_counts.items(), key=lambda x: -x[1]):
            print(f"    {count:4d}  {label}")
    else:
        print("    No topic matches found in recent articles.")

    return tagged


# ── Performance snapshot articles ─────────────────────────────────────────────
def _is_wasatch_back_agent(agent):
    """Heuristic: is this agent Wasatch Back?"""
    brokerage = (agent.get("brokerage_name") or "").lower()
    service_areas = (agent.get("zillow_service_areas") or "").lower()
    combined = brokerage + " " + service_areas

    wasatch_keywords = {
        "park city", "summit county", "wasatch county", "heber", "midway",
        "kamas", "coalville", "deer valley", "canyons", "84060", "84098",
        "84032", "84036"
    }
    return any(kw in combined for kw in wasatch_keywords)


def generate_performance_snapshot(sqlite_conn, agents, dry_run=False):
    """
    Generate agent-intel articles from realtrends_rank and zillow_sales_12mo.
    Creates one article per qualifying agent with notable performance data.
    Returns count of articles created.
    """
    now = now_iso()
    created = 0

    # ── RealTrends ranked agents ──
    ranked = [a for a in agents if a.get("realtrends_rank") and _is_wasatch_back_agent(a)]
    ranked.sort(key=lambda a: a["realtrends_rank"])

    if ranked:
        # One aggregate article for all RealTrends-ranked Wasatch Back agents
        names_list = ", ".join(
            f"{a['full_name']} ({a['brokerage_name'] or 'independent'}, #{a['realtrends_rank']})"
            for a in ranked[:10]
        )
        total = len(ranked)
        title = f"Wasatch Back Agents in RealTrends Utah Rankings — {total} Agents Identified"
        summary = (
            f"The Longitude Network database includes {total} Wasatch Back agents who appear in "
            f"RealTrends Utah production rankings. Top-ranked agents: {names_list}. "
            f"Use these rankings to prioritize agent outreach, content targeting, and intelligence briefs."
        )
        article_uid = uid(SOURCE_LABEL, "realtrends-snapshot", now[:10])
        if not dry_run:
            sqlite_conn.execute("""
                INSERT OR IGNORE INTO articles
                  (uid, source, category, market_area, title, link, summary, published, fetched_at, status, notes)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (
                article_uid, SOURCE_LABEL, "agent-intel", "Wasatch Back",
                title, "#longitude-sync",
                summary, now, now, "new",
                f"Auto-generated by longitude_sync.py — {total} ranked agents"
            ))
            if sqlite_conn.execute("SELECT changes()").fetchone()[0] > 0:
                created += 1
                print(f"  ✓  Created: {title}")
        else:
            print(f"  [dry-run] Would create: {title}")
            print(f"            {summary[:120]}…")
            created += 1

    # ── Top producers by Zillow sales volume ──
    top_producers = [
        a for a in agents
        if a.get("zillow_sales_12mo") and a["zillow_sales_12mo"] >= 5
        and _is_wasatch_back_agent(a)
    ]
    top_producers.sort(key=lambda a: a["zillow_sales_12mo"], reverse=True)

    if top_producers:
        top20 = top_producers[:20]
        names_vol = ", ".join(
            f"{a['full_name']} ({a['zillow_sales_12mo']} sales)"
            for a in top20[:8]
        )
        title2 = f"Wasatch Back Top Producers by Zillow Sales — {len(top_producers)} Active Agents Tracked"
        summary2 = (
            f"Longitude Network is tracking {len(top_producers)} Wasatch Back agents with 5+ Zillow-reported "
            f"sales in the past 12 months. Leading producers: {names_vol}. "
            f"This data informs agent-specific content, relationship prioritization, and market share analysis."
        )
        article_uid2 = uid(SOURCE_LABEL, "zillow-top-producers", now[:10])
        if not dry_run:
            sqlite_conn.execute("""
                INSERT OR IGNORE INTO articles
                  (uid, source, category, market_area, title, link, summary, published, fetched_at, status, notes)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (
                article_uid2, SOURCE_LABEL, "agent-intel", "Wasatch Back",
                title2, "#longitude-sync",
                summary2, now, now, "new",
                f"Auto-generated by longitude_sync.py — {len(top_producers)} active producers"
            ))
            if sqlite_conn.execute("SELECT changes()").fetchone()[0] > 0:
                created += 1
                print(f"  ✓  Created: {title2}")
        else:
            print(f"  [dry-run] Would create: {title2}")
            print(f"            {summary2[:120]}…")
            created += 1

    # ── Individual agent articles for high-value agents ──
    # Only generate for agents with both realtrends_rank AND high zillow_sales
    vip_agents = [
        a for a in agents
        if a.get("realtrends_rank")
        and a.get("zillow_sales_12mo") and a["zillow_sales_12mo"] >= 10
        and _is_wasatch_back_agent(a)
    ]

    for agent in vip_agents[:25]:  # cap at 25 individual articles
        brokerage = agent.get("brokerage_name") or "Independent"
        sales = agent.get("zillow_sales_12mo", 0)
        rank = agent.get("realtrends_rank", "unranked")
        title3 = f"Agent Profile: {agent['full_name']} — #{rank} RealTrends Utah, {sales} Zillow Sales"
        summary3 = (
            f"{agent['full_name']} ({brokerage}) ranks #{rank} in RealTrends Utah and has recorded "
            f"{sales} transactions in the past 12 months on Zillow. "
        )
        if agent.get("zillow_price_range_max"):
            summary3 += f"Price range ceiling: ${agent['zillow_price_range_max']:,.0f}. "
        if agent.get("specialty"):
            summary3 += f"Specialty: {agent['specialty']}. "
        if agent.get("ig_handle"):
            summary3 += f"Instagram: @{agent['ig_handle']}."

        article_uid3 = uid(SOURCE_LABEL, "agent-profile", agent["email"])
        if not dry_run:
            sqlite_conn.execute("""
                INSERT OR IGNORE INTO articles
                  (uid, source, category, market_area, title, link, summary, published, fetched_at, status, notes)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (
                article_uid3, SOURCE_LABEL, "agent-intel", "Wasatch Back",
                title3, agent.get("agent_website") or "#longitude-sync",
                summary3, now, now, "new",
                f"Auto-generated by longitude_sync.py — {brokerage}"
            ))
            if sqlite_conn.execute("SELECT changes()").fetchone()[0] > 0:
                created += 1
        else:
            print(f"  [dry-run] VIP agent: {agent['full_name']} (#{rank}, {sales} sales)")

    if vip_agents and not dry_run:
        print(f"  ✓  Created {len(vip_agents)} individual agent profile articles")
        created += len(vip_agents)

    if not dry_run:
        sqlite_conn.commit()

    return created


# ── Network stats article ─────────────────────────────────────────────────────
def generate_network_stats(sqlite_conn, agents, dry_run=False):
    """
    Generate a market-data article summarizing agent/brokerage distribution
    for the Wasatch Back market from the Longitude Network database.
    """
    wasatch_agents = [a for a in agents if _is_wasatch_back_agent(a)]

    # Brokerage counts
    from collections import Counter
    brokerage_counts = Counter(
        (a.get("brokerage_name") or "Independent").strip()
        for a in wasatch_agents
        if a.get("brokerage_name")
    )
    top_brokerages = brokerage_counts.most_common(10)

    total = len(wasatch_agents)
    now = now_iso()

    broker_summary = ", ".join(
        f"{name} ({count})" for name, count in top_brokerages[:6]
    )

    title = f"Wasatch Back Agent Landscape — {total} Agents Tracked Across {len(brokerage_counts)} Brokerages"
    summary = (
        f"The Longitude Network database currently tracks {total} active agents operating in the "
        f"Wasatch Back market (Park City, Summit County, Wasatch County, Heber Valley). "
        f"Brokerage distribution (top firms): {broker_summary}. "
        f"Total brokerage firms represented: {len(brokerage_counts)}. "
        f"Use this data to track market share shifts and identify outreach opportunities."
    )

    article_uid = uid(SOURCE_LABEL, "network-stats", now[:10])

    if dry_run:
        print(f"\n  [dry-run] Network stats article:")
        print(f"    Title:   {title}")
        print(f"    Summary: {summary[:160]}…")
        print(f"\n  Top brokerages in Wasatch Back:")
        for name, count in top_brokerages:
            print(f"    {count:4d}  {name}")
        return 1

    sqlite_conn.execute("""
        INSERT OR IGNORE INTO articles
          (uid, source, category, market_area, title, link, summary, published, fetched_at, status, notes)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (
        article_uid, SOURCE_LABEL, "market-data", "Wasatch Back",
        title, "#longitude-sync",
        summary, now, now, "new",
        f"Auto-generated by longitude_sync.py — {total} agents, {len(brokerage_counts)} brokerages"
    ))
    created = sqlite_conn.execute("SELECT changes()").fetchone()[0]
    sqlite_conn.commit()

    if created:
        print(f"  ✓  Created network stats article: {title}")
    else:
        print(f"  ↩  Network stats article already exists for today (skipped)")

    return created


# ── Stats / reporting ─────────────────────────────────────────────────────────
def show_stats(sqlite_conn):
    """Print a summary of the integration state."""
    print("\n  Longitude Sync — Database Stats")
    print("  " + "─" * 40)

    # Agent cache
    row = sqlite_conn.execute("SELECT COUNT(*), MAX(last_synced) FROM longitude_agents").fetchone()
    print(f"  Cached agents:       {row[0]:>6,}")
    print(f"  Last sync:           {(row[1] or 'never')[:19]}")

    # Links
    row2 = sqlite_conn.execute("SELECT COUNT(*), COUNT(DISTINCT agent_email) FROM article_agent_links").fetchone()
    print(f"  Article-agent links: {row2[0]:>6,}  ({row2[1]} unique agents)")

    # Auto-generated articles
    row3 = sqlite_conn.execute(
        "SELECT COUNT(*) FROM articles WHERE source = ?", (SOURCE_LABEL,)
    ).fetchone()
    print(f"  Synced articles:     {row3[0]:>6,}")

    # Top-linked agents
    top = sqlite_conn.execute("""
        SELECT agent_name, COUNT(*) AS c
        FROM article_agent_links
        GROUP BY agent_email
        ORDER BY c DESC LIMIT 10
    """).fetchall()
    if top:
        print("\n  Most-mentioned agents:")
        for name, count in top:
            print(f"    {count:3d}  {name}")

    print()


def show_agent(sqlite_conn, name_query, agents):
    """Show all articles mentioning a specific agent."""
    name_lower = name_query.lower()
    matched = [a for a in agents if name_lower in a["full_name"].lower()]

    if not matched:
        print(f"  No agent found matching '{name_query}'")
        return

    for agent in matched[:3]:
        print(f"\n  Agent: {agent['full_name']} ({agent.get('brokerage_name', '')})")
        print(f"  Email: {agent['email']}")
        if agent.get("realtrends_rank"):
            print(f"  RealTrends rank: #{agent['realtrends_rank']}")
        if agent.get("zillow_sales_12mo"):
            print(f"  Zillow sales (12mo): {agent['zillow_sales_12mo']}")

        links = sqlite_conn.execute("""
            SELECT a.title, a.source, a.published, aal.match_type, aal.match_score
            FROM article_agent_links aal
            JOIN articles a ON a.uid = aal.article_uid
            WHERE aal.agent_email = ?
            ORDER BY a.published DESC LIMIT 10
        """, (agent["email"],)).fetchall()

        if links:
            print(f"  Articles mentioning this agent ({len(links)}):")
            for title, source, pub, mtype, score in links:
                print(f"    [{mtype} {score:.2f}] {title[:60]} — {source} ({(pub or '')[:10]})")
        else:
            print("  No article mentions found yet.")


# ── Main ──────────────────────────────────────────────────────────────────────
def run(scan=True, performance=True, network=True, dry_run=False, agent_name=None):
    sqlite_conn = sqlite3.connect(SQLITE_DB, timeout=30)
    init_sqlite_extensions(sqlite_conn)

    now = now_iso()
    print(f"\n{'='*60}")
    print(f"  Longitude Network ↔ Agent Intelligencer Sync")
    print(f"  Run: {now[:19]}Z")
    if dry_run:
        print(f"  Mode: DRY RUN — no changes will be saved")
    print(f"{'='*60}\n")

    # Load agents
    print("  Loading agents from Longitude Network PostgreSQL…")
    agents = load_agents_from_pg()

    if not dry_run:
        cache_agents_in_sqlite(sqlite_conn, agents)

    if agent_name:
        show_agent(sqlite_conn, agent_name, agents)
        sqlite_conn.close()
        return

    total_links    = 0
    total_articles = 0
    total_tagged   = 0

    if scan:
        print("\n  [1/4] Topic Relevance Tagging")
        total_tagged = scan_articles_for_topics(sqlite_conn, dry_run=dry_run)
        print(f"  → {total_tagged} articles tagged as agent-relevant")

        print("\n  [2/4] Agent Name Detection")
        total_links = scan_articles_for_agents(sqlite_conn, agents, dry_run=dry_run)
        print(f"  → {total_links} new article-agent links created")

    if performance:
        print("\n  [3/4] Performance Snapshot Articles")
        total_articles += generate_performance_snapshot(sqlite_conn, agents, dry_run=dry_run)
        print(f"  → {total_articles} agent-intel articles created/updated")

    if network:
        print("\n  [4/4] Network Stats Article (market-data)")
        n = generate_network_stats(sqlite_conn, agents, dry_run=dry_run)
        total_articles += n

    print(f"\n{'='*60}")
    print(f"  Sync complete")
    print(f"  Articles tagged:  {total_tagged}")
    print(f"  Articles created: {total_articles}")
    print(f"  Agent links:      {total_links}")
    print(f"{'='*60}\n")

    sqlite_conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Longitude Network ↔ Agent Intelligencer sync"
    )
    parser.add_argument("--scan-articles",       action="store_true",
                        help="Topic tagging + agent name detection only")
    parser.add_argument("--topics",              action="store_true",
                        help="Topic relevance tagging only (no PG connection needed)")
    parser.add_argument("--performance-snapshot", action="store_true",
                        help="Generate performance snapshot articles only")
    parser.add_argument("--network-stats",        action="store_true",
                        help="Generate network stats article only")
    parser.add_argument("--dry-run",              action="store_true",
                        help="Print what would happen without saving anything")
    parser.add_argument("--stats",                action="store_true",
                        help="Show current integration stats and exit")
    parser.add_argument("--agent",                type=str, default=None,
                        help="Show all articles mentioning a specific agent")
    args = parser.parse_args()

    # If --stats, just show stats (no PG connection needed)
    if args.stats:
        sqlite_conn = sqlite3.connect(SQLITE_DB, timeout=30)
        init_sqlite_extensions(sqlite_conn)
        show_stats(sqlite_conn)
        sqlite_conn.close()
        sys.exit(0)

    # If --topics, tag articles by topic without touching PostgreSQL
    if args.topics:
        sqlite_conn = sqlite3.connect(SQLITE_DB, timeout=30)
        init_sqlite_extensions(sqlite_conn)
        now = now_iso()
        print(f"\n{'='*60}")
        print(f"  Topic Relevance Tagging (no PG connection)")
        print(f"  Run: {now[:19]}Z")
        if args.dry_run:
            print(f"  Mode: DRY RUN")
        print(f"{'='*60}\n")
        tagged = scan_articles_for_topics(sqlite_conn, dry_run=args.dry_run)
        print(f"\n  → {tagged} articles tagged\n")
        sqlite_conn.close()
        sys.exit(0)

    # Determine which modes to run
    any_mode = args.scan_articles or args.performance_snapshot or args.network_stats
    run(
        scan        = args.scan_articles       if any_mode else True,
        performance = args.performance_snapshot if any_mode else True,
        network     = args.network_stats        if any_mode else True,
        dry_run     = args.dry_run,
        agent_name  = args.agent,
    )
