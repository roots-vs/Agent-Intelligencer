#!/usr/bin/env python3
"""
digest_generator.py — Weekly personalized agent email digests
-------------------------------------------------------------
Generates one HTML email per agent in output/digests/, named by email.

Each digest contains:
  Section 1: Articles mentioning the agent by name (article_agent_links)
  Section 2: Top local-market and agent-intel articles from Wasatch Back
  Section 3: One national brokerage or industry-practice article
  Footer:    Unsubscribe link + "powered by Longitude Intelligence"

Usage:
    python3 digest_generator.py                        # all agents with links
    python3 digest_generator.py --agent matt@email.com # one agent
    python3 digest_generator.py --preview              # first 3 agents, open in browser
    python3 digest_generator.py --days 7               # articles from last N days (default 7)
    python3 digest_generator.py --all-agents           # include agents with no mentions
    python3 digest_generator.py --dry-run              # print counts, no files written
"""

import sqlite3
import argparse
import json
import os
import sys
import subprocess
from datetime import datetime, timezone, timedelta
from html import escape

# ── Config ────────────────────────────────────────────────────────────────────

SQLITE_DB   = os.path.expanduser("~/.longitude/curation.db")
OUTPUT_DIR  = os.path.join(os.path.dirname(__file__), "output", "digests")
BRAND_NAME  = "Utah Mountain Living — Longitude Market Intelligence"
BRAND_COLOR = "#1a3c5e"   # deep navy
ACCENT      = "#c9a84c"   # gold
BG_LIGHT    = "#f8f6f1"

# Max articles per section
MAX_MENTIONS    = 6
MAX_LOCAL       = 4
MAX_NATIONAL    = 1

# ── DB helpers ────────────────────────────────────────────────────────────────

def db():
    conn = sqlite3.connect(SQLITE_DB)
    conn.row_factory = sqlite3.Row
    return conn


def cutoff_iso(days: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.isoformat()


# ── Data queries ──────────────────────────────────────────────────────────────

def agents_with_links(conn) -> list:
    """Agents that appear in article_agent_links (have at least one mention)."""
    rows = conn.execute("""
        SELECT DISTINCT
            aal.agent_email   AS email,
            aal.agent_name    AS full_name,
            COALESCE(la.brokerage_name, '') AS brokerage_name,
            COALESCE(la.ig_handle, '')      AS ig_handle
        FROM article_agent_links aal
        LEFT JOIN longitude_agents la ON la.email = aal.agent_email
        ORDER BY aal.agent_name
    """).fetchall()
    return [dict(r) for r in rows]


def all_agents(conn) -> list:
    """All agents in longitude_agents table."""
    rows = conn.execute("""
        SELECT
            email,
            COALESCE(full_name, email) AS full_name,
            COALESCE(brokerage_name, '') AS brokerage_name,
            COALESCE(ig_handle, '')      AS ig_handle
        FROM longitude_agents
        ORDER BY full_name
    """).fetchall()
    return [dict(r) for r in rows]


def one_agent(conn, email: str) -> dict | None:
    row = conn.execute("""
        SELECT
            email,
            COALESCE(full_name, email) AS full_name,
            COALESCE(brokerage_name, '') AS brokerage_name,
            COALESCE(ig_handle, '')      AS ig_handle
        FROM longitude_agents
        WHERE LOWER(email) = LOWER(?)
    """, (email,)).fetchone()
    if row:
        return dict(row)
    # Fall back to article_agent_links if not in longitude_agents
    row = conn.execute("""
        SELECT DISTINCT
            agent_email AS email,
            agent_name  AS full_name,
            '' AS brokerage_name,
            '' AS ig_handle
        FROM article_agent_links
        WHERE LOWER(agent_email) = LOWER(?)
        LIMIT 1
    """, (email,)).fetchone()
    return dict(row) if row else None


def agent_mentions(conn, email: str, cutoff: str) -> list:
    """Articles mentioning this agent — external URLs only (skips auto-generated internal links)."""
    rows = conn.execute("""
        SELECT
            a.title, a.link, a.summary, a.source,
            a.published, a.category, aal.match_type
        FROM article_agent_links aal
        JOIN articles a ON a.uid = aal.article_uid
        WHERE LOWER(aal.agent_email) = LOWER(?)
          AND a.status != 'skip'
          AND a.link LIKE 'http%'
          AND (a.published >= ? OR a.fetched_at >= ?)
        ORDER BY a.published DESC
        LIMIT ?
    """, (email, cutoff, cutoff, MAX_MENTIONS)).fetchall()
    return [dict(r) for r in rows]


def local_articles(conn, cutoff: str, exclude_uids: list) -> list:
    """Top local-market and agent-intel articles, excluding already-shown ones."""
    placeholders = ",".join("?" * len(exclude_uids)) if exclude_uids else "''"
    params = [cutoff, cutoff] + exclude_uids + [MAX_LOCAL]
    rows = conn.execute(f"""
        SELECT title, link, summary, source, published, category
        FROM articles
        WHERE category IN ('local-market', 'agent-intel')
          AND market_area = 'Wasatch Back'
          AND status != 'skip'
          AND (published >= ? OR fetched_at >= ?)
          {"AND uid NOT IN (" + placeholders + ")" if exclude_uids else ""}
        ORDER BY published DESC
        LIMIT ?
    """, params).fetchall()
    return [dict(r) for r in rows]


def national_article(conn, cutoff: str, exclude_uids: list) -> dict | None:
    """One top national-brokerage or industry-practice article."""
    placeholders = ",".join("?" * len(exclude_uids)) if exclude_uids else "''"
    params = [cutoff, cutoff] + exclude_uids + [MAX_NATIONAL]
    rows = conn.execute(f"""
        SELECT title, link, summary, source, published, category
        FROM articles
        WHERE category IN ('national-brokerage', 'industry-practice')
          AND status != 'skip'
          AND (published >= ? OR fetched_at >= ?)
          {"AND uid NOT IN (" + placeholders + ")" if exclude_uids else ""}
        ORDER BY published DESC
        LIMIT ?
    """, params).fetchall()
    return dict(rows[0]) if rows else None


def mention_uids(conn, email: str, cutoff: str) -> list:
    """UIDs of articles in agent_mentions — for exclude list."""
    rows = conn.execute("""
        SELECT a.uid
        FROM article_agent_links aal
        JOIN articles a ON a.uid = aal.article_uid
        WHERE LOWER(aal.agent_email) = LOWER(?)
          AND (a.published >= ? OR a.fetched_at >= ?)
    """, (email, cutoff, cutoff)).fetchall()
    return [r[0] for r in rows]


def active_market_peers(conn, agent: dict, limit: int = 3) -> list:
    """Top producers who share service areas with this agent (from longitude_agents cache)."""
    row = conn.execute(
        "SELECT zillow_service_areas FROM longitude_agents WHERE LOWER(email) = LOWER(?)",
        (agent["email"],)
    ).fetchone()

    agent_areas: set = set()
    if row and row[0]:
        try:
            data = json.loads(row[0])
            if isinstance(data, list):
                agent_areas = {str(a).lower().strip() for a in data}
        except Exception:
            pass

    if not agent_areas:
        agent_areas = {"park city", "summit county", "wasatch county", "heber"}

    candidates = conn.execute("""
        SELECT full_name, brokerage_name, zillow_sales_12mo, zillow_service_areas
        FROM longitude_agents
        WHERE zillow_sales_12mo > 0
          AND LOWER(email) != LOWER(?)
        ORDER BY zillow_sales_12mo DESC
        LIMIT 300
    """, (agent["email"],)).fetchall()

    peers = []
    for full_name, brokerage, sales, areas_json in candidates:
        if not areas_json:
            continue
        try:
            data = json.loads(areas_json)
            peer_areas = {str(a).lower().strip() for a in data} if isinstance(data, list) else set()
        except Exception:
            continue
        if agent_areas & peer_areas:
            peers.append({
                "full_name":       full_name or "",
                "brokerage_name":  brokerage or "",
                "zillow_sales_12mo": sales or 0,
            })
        if len(peers) >= limit:
            break

    return peers


# ── HTML rendering ─────────────────────────────────────────────────────────────

def fmt_date(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%B %-d, %Y")
    except Exception:
        return iso[:10]


CATEGORY_LABELS = {
    "local-market":       "Local Market",
    "agent-intel":        "Agent Intel",
    "national-brokerage": "Brokerage News",
    "industry-practice":  "Industry Practice",
    "market-data":        "Market Data",
    "comp-market":        "Comp Market",
}


def pill(category: str) -> str:
    label = CATEGORY_LABELS.get(category, category)
    colors = {
        "local-market":       ("#e8f4fd", "#1a6e9e"),
        "agent-intel":        ("#fdf3e8", "#b86b1a"),
        "national-brokerage": ("#f0eef8", "#5a4a8a"),
        "industry-practice":  ("#edf7ed", "#2e7d32"),
        "market-data":        ("#fef9e8", "#a07c00"),
        "comp-market":        ("#fde8ef", "#9e1a3a"),
    }
    bg, fg = colors.get(category, ("#eeeeee", "#444444"))
    return (
        f'<span style="display:inline-block;padding:2px 8px;border-radius:12px;'
        f'font-size:11px;font-weight:600;letter-spacing:.4px;'
        f'background:{bg};color:{fg};">{escape(label)}</span>'
    )


def valid_url(link: str | None) -> bool:
    return bool(link and link.startswith("http"))


def article_card(article: dict, highlight: bool = False) -> str:
    border = f"border-left:3px solid {ACCENT};padding-left:14px;" if highlight else ""
    date_str = fmt_date(article.get("published"))
    source   = escape(article.get("source") or "")
    summary  = escape((article.get("summary") or "")[:260])
    if len(article.get("summary") or "") > 260:
        summary += "…"
    link        = article.get("link") or ""
    is_internal = not valid_url(link)  # render title as plain text when no real URL

    return f"""
    <div style="margin-bottom:20px;{border}">
      <div style="margin-bottom:4px;">
        {pill(article.get("category",""))}
        <span style="font-size:11px;color:#888;margin-left:8px;">{escape(date_str)} · {source}</span>
      </div>
      <div style="font-size:16px;font-weight:600;line-height:1.35;margin-bottom:5px;">
        {"<span style='color:#1a3c5e;'>" + escape(article.get("title","")) + "</span>" if is_internal
         else f'<a href="{escape(link)}" style="color:{BRAND_COLOR};text-decoration:none;">{escape(article.get("title",""))}</a>'}
      </div>
      {"" if not summary else f'<div style="font-size:14px;color:#555;line-height:1.5;">{summary}</div>'}
    </div>
    """


def section_header(title: str, subtitle: str = "") -> str:
    sub = f'<div style="font-size:13px;color:#888;margin-top:2px;">{escape(subtitle)}</div>' if subtitle else ""
    return f"""
    <div style="border-top:2px solid {ACCENT};padding-top:12px;margin:32px 0 18px;">
      <div style="font-size:13px;font-weight:700;letter-spacing:1.2px;
                  text-transform:uppercase;color:{ACCENT};">{escape(title)}</div>
      {sub}
    </div>
    """


def peers_table(peers: list) -> str:
    if not peers:
        return '<p style="color:#888;font-size:14px;font-style:italic;">No peer data available.</p>'
    rows = ""
    for p in peers:
        rows += (
            f'<tr>'
            f'<td style="padding:7px 10px;font-size:14px;font-weight:600;color:#222;">'
            f'{escape(p["full_name"])}</td>'
            f'<td style="padding:7px 10px;font-size:13px;color:#555;">'
            f'{escape(p["brokerage_name"])}</td>'
            f'<td style="padding:7px 10px;font-size:13px;color:#555;text-align:right;">'
            f'{p["zillow_sales_12mo"]} txns</td>'
            f'</tr>'
        )
    return (
        f'<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">'
        f'<thead><tr style="background:#f4f1eb;">'
        f'<th style="padding:6px 10px;font-size:11px;font-weight:700;letter-spacing:.5px;'
        f'text-transform:uppercase;color:#888;text-align:left;">Agent</th>'
        f'<th style="padding:6px 10px;font-size:11px;font-weight:700;letter-spacing:.5px;'
        f'text-transform:uppercase;color:#888;text-align:left;">Brokerage</th>'
        f'<th style="padding:6px 10px;font-size:11px;font-weight:700;letter-spacing:.5px;'
        f'text-transform:uppercase;color:#888;text-align:right;">Volume</th>'
        f'</tr></thead><tbody>{rows}</tbody></table>'
    )


def render_digest(agent: dict, mentions: list, local: list, national: dict | None,
                  week_label: str, peers: list | None = None) -> str:
    name      = escape(agent["full_name"])
    brokerage = escape(agent.get("brokerage_name") or "")
    email     = escape(agent["email"])

    mention_html = "".join(article_card(a, highlight=True) for a in mentions) if mentions else (
        '<p style="color:#888;font-size:14px;font-style:italic;">'
        'No direct mentions this week — keep an eye on the market.</p>'
    )

    local_html = "".join(article_card(a) for a in local) if local else (
        '<p style="color:#888;font-size:14px;font-style:italic;">No new local stories this period.</p>'
    )

    national_html = article_card(national) if national else (
        '<p style="color:#888;font-size:14px;font-style:italic;">No national stories this period.</p>'
    )

    brokerage_line = f"<br><span style='color:#888;font-size:13px;'>{brokerage}</span>" if brokerage else ""

    peers_html = peers_table(peers or [])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Wasatch Back Intelligence Brief — {name}</title>
</head>
<body style="margin:0;padding:0;background:{BG_LIGHT};font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">

<table width="100%" cellpadding="0" cellspacing="0" style="background:{BG_LIGHT};">
<tr><td align="center" style="padding:32px 16px;">

<table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background:#fff;border-radius:6px;overflow:hidden;box-shadow:0 1px 6px rgba(0,0,0,.08);">

  <!-- Header -->
  <tr><td style="background:{BRAND_COLOR};padding:28px 36px;">
    <div style="font-size:32px;font-weight:800;letter-spacing:4px;text-transform:uppercase;color:#fff;line-height:1;">
      LONGITUDE
    </div>
    <div style="font-size:12px;font-weight:600;letter-spacing:2px;text-transform:uppercase;color:{ACCENT};margin-top:6px;">
      Wasatch Back Intelligence
    </div>
    <div style="font-size:13px;color:rgba(255,255,255,.55);margin-top:10px;">{escape(week_label)}</div>
  </td></tr>

  <!-- Salutation -->
  <tr><td style="padding:28px 36px 0;">
    <div style="font-size:17px;color:#222;line-height:1.5;">
      {name}{brokerage_line}
    </div>
    <div style="font-size:15px;color:#555;margin-top:10px;line-height:1.6;">
      Here's your Wasatch Back intelligence brief for this week.
    </div>
  </td></tr>

  <!-- Body -->
  <tr><td style="padding:8px 36px 28px;">

    {section_header("You're in the news", f"{len(mentions)} mention{'s' if len(mentions) != 1 else ''} this week") if mentions else section_header("In the news", "No direct mentions this week")}
    {mention_html}

    {section_header("Wasatch Back Market", "Local market · agent intelligence")}
    {local_html}

    {section_header("Industry Pulse", "National brokerage & practice news")}
    {national_html}

    {section_header("Active in Your Market", "Top producers with shared service areas (Zillow, last 12 months)")}
    {peers_html}

  </td></tr>

  <!-- Footer -->
  <tr><td style="background:#f0ede6;padding:20px 36px;border-top:1px solid #ddd;">
    <div style="font-size:12px;color:#888;line-height:1.7;">
      You're receiving this because you're part of the Longitude Intelligence network.<br>
      <a href="mailto:{email}?subject=Unsubscribe" style="color:{BRAND_COLOR};">Unsubscribe</a>
      &nbsp;·&nbsp;
      <a href="https://longitudeintelligence.com" style="color:{BRAND_COLOR};">longitudeintelligence.com</a>
      <br><br>
      <strong style="color:{BRAND_COLOR};">Cameron Brockbank</strong>
      &nbsp;·&nbsp; VP Sales
      &nbsp;·&nbsp;
      <a href="mailto:cameron@longitudepm.com" style="color:{BRAND_COLOR};">cameron@longitudepm.com</a>
      <br>
      <strong style="color:{BRAND_COLOR};">Powered by {escape(BRAND_NAME)}</strong>
      &nbsp;·&nbsp; Wasatch Back Real Estate Intelligence
    </div>
  </td></tr>

</table>
</td></tr>
</table>

</body>
</html>"""


# ── Main ──────────────────────────────────────────────────────────────────────

def week_label(days: int) -> str:
    end   = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    if end.month == start.month:
        return f"{start.strftime('%B %-d')}–{end.strftime('%-d, %Y')}"
    return f"{start.strftime('%B %-d')} – {end.strftime('%B %-d, %Y')}"


def build_digest(conn, agent: dict, cutoff: str) -> str:
    email     = agent["email"]
    uids_seen = mention_uids(conn, email, cutoff)
    mentions  = agent_mentions(conn, email, cutoff)
    local     = local_articles(conn, cutoff, uids_seen)
    national  = national_article(conn, cutoff, uids_seen)
    peers     = active_market_peers(conn, agent)
    label     = week_label(7)
    return render_digest(agent, mentions, local, national, label, peers)


def write_digest(html: str, email: str) -> str:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    safe = (email or "unknown").replace("@", "_at_").replace("/", "_").strip() or "unknown"
    path = os.path.join(OUTPUT_DIR, f"{safe}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


def main():
    parser = argparse.ArgumentParser(description="Generate weekly agent digest emails")
    parser.add_argument("--agent",       help="One agent email address")
    parser.add_argument("--preview",     action="store_true", help="First 3 agents, open in browser")
    parser.add_argument("--days",        type=int, default=7, help="Look-back window in days (default 7)")
    parser.add_argument("--all-agents",  action="store_true", help="Include agents with no mentions")
    parser.add_argument("--dry-run",     action="store_true", help="Print counts without writing files")
    args = parser.parse_args()

    conn   = db()
    cutoff = cutoff_iso(args.days)

    # Resolve agent list
    if args.agent:
        agent = one_agent(conn, args.agent)
        if not agent:
            print(f"Agent not found: {args.agent}", file=sys.stderr)
            sys.exit(1)
        agents = [agent]
    elif args.all_agents:
        agents = all_agents(conn)
    else:
        agents = agents_with_links(conn)

    if args.preview:
        agents = agents[:3]

    if not agents:
        print("No agents to process.")
        return

    print(f"Generating digests for {len(agents)} agent(s) · last {args.days} days …")

    written   = []
    skipped   = 0

    for agent in agents:
        mentions_count = len(agent_mentions(conn, agent["email"], cutoff))
        local_count    = len(local_articles(conn, cutoff, []))
        national_count = 1 if national_article(conn, cutoff, []) else 0
        total          = mentions_count + local_count + national_count

        if args.dry_run:
            print(f"  {agent['full_name']} <{agent['email']}> — "
                  f"{mentions_count} mention(s), {local_count} local, {national_count} national")
            continue

        html = build_digest(conn, agent, cutoff)
        path = write_digest(html, agent["email"])
        written.append(path)
        print(f"  ✓ {agent['full_name']} → {os.path.basename(path)}  ({total} articles)")

    conn.close()

    if args.dry_run:
        return

    print(f"\nWrote {len(written)} digest(s) to {OUTPUT_DIR}/")

    if args.preview and written:
        for path in written[:3]:
            if sys.platform == "darwin":
                subprocess.run(["open", path])
            else:
                print(f"  Open: {path}")


if __name__ == "__main__":
    main()
