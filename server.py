#!/usr/bin/env python3
"""
server.py — Utah Mountain Living — Longitude Market Intelligence Curation Dashboard
-----------------------------------------------------
Starts a local web server at http://localhost:8765 with a review dashboard.
All data stays on your machine — nothing is sent to the internet.

Usage:
    python server.py              # starts server, opens browser
    python server.py --port 9000  # use a different port
    python server.py --no-open    # don't auto-open browser
"""

import sqlite3
import json
import os
import argparse
import webbrowser
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime

DB_PATH   = os.path.join(os.path.dirname(__file__), "curation.db")
HTML_PATH = os.path.join(os.path.dirname(__file__), "dashboard.html")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # suppress default access log noise

    def send_json(self, data, status=200):
        body = json.dumps(data, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, path):
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path
        params = parse_qs(parsed.query)

        # ── Dashboard HTML ──
        if path in ("/", "/dashboard"):
            self.send_html(HTML_PATH)
            return

        # ── Articles API ──
        if path == "/api/articles":
            conn = get_conn()
            status_filter   = params.get("status",   ["all"])[0]
            category_filter = params.get("category", ["all"])[0]
            days_filter     = int(params.get("days",  [14])[0])
            market_filter   = params.get("market",   ["all"])[0]

            query = """
                SELECT id, source, category, market_area, title, link, summary,
                       published, status, issue_tag, notes
                FROM articles
                WHERE 1=1
            """
            args = []
            if status_filter != "all":
                query += " AND status = ?"
                args.append(status_filter)
            if category_filter != "all":
                query += " AND category = ?"
                args.append(category_filter)
            if market_filter != "all":
                query += " AND market_area = ?"
                args.append(market_filter)
            if days_filter > 0:
                query += " AND (published IS NULL OR published >= datetime('now', ?))"
                args.append(f"-{days_filter} days")
            query += " ORDER BY published DESC NULLS LAST LIMIT 500"

            rows = conn.execute(query, args).fetchall()
            conn.close()
            self.send_json([dict(r) for r in rows])
            return

        # ── Stats API ──
        if path == "/api/stats":
            conn = get_conn()
            stats = {}
            for row in conn.execute("SELECT status, COUNT(*) as n FROM articles GROUP BY status"):
                stats[row["status"]] = row["n"]
            stats["total"] = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
            last_fetch = conn.execute("SELECT MAX(run_at) FROM fetch_log").fetchone()[0]
            stats["last_fetch"] = last_fetch
            sources = conn.execute(
                "SELECT source, COUNT(*) as n FROM articles GROUP BY source ORDER BY n DESC"
            ).fetchall()
            stats["sources"] = [{"source": r["source"], "count": r["n"]} for r in sources]
            conn.close()
            self.send_json(stats)
            return

        # ── Export selected articles ──
        if path == "/api/export":
            conn = get_conn()
            rows = conn.execute("""
                SELECT id, source, category, market_area, title, link, summary,
                       published, issue_tag, notes
                FROM articles WHERE status = 'selected'
                ORDER BY category, published DESC
            """).fetchall()
            conn.close()
            self.send_json([dict(r) for r in rows])
            return

        # ── Latest market stats (for Compose panel) ──
        if path == "/api/market-stats":
            conn = get_conn()
            try:
                rows = conn.execute("""
                    SELECT area, metric, value, period, source, fetched_at
                    FROM market_stats
                    ORDER BY fetched_at DESC
                    LIMIT 20
                """).fetchall()
                conn.close()
                self.send_json([dict(r) for r in rows])
            except Exception:
                conn.close()
                self.send_json([])
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        body   = json.loads(self.rfile.read(length)) if length else {}

        # ── Update article status ──
        if parsed.path == "/api/update":
            article_id = body.get("id")
            new_status  = body.get("status")
            issue_tag   = body.get("issue_tag")
            notes       = body.get("notes")
            if not article_id or not new_status:
                self.send_json({"error": "id and status required"}, 400)
                return
            conn = get_conn()
            conn.execute("""
                UPDATE articles
                SET status = ?, issue_tag = COALESCE(?, issue_tag), notes = COALESCE(?, notes)
                WHERE id = ?
            """, (new_status, issue_tag, notes, article_id))
            conn.commit()
            conn.close()
            self.send_json({"ok": True})
            return

        # ── Bulk update ──
        if parsed.path == "/api/bulk-update":
            ids        = body.get("ids", [])
            new_status = body.get("status")
            if not ids or not new_status:
                self.send_json({"error": "ids and status required"}, 400)
                return
            conn = get_conn()
            conn.executemany("UPDATE articles SET status = ? WHERE id = ?",
                             [(new_status, i) for i in ids])
            conn.commit()
            conn.close()
            self.send_json({"ok": True, "updated": len(ids)})
            return

        self.send_response(404)
        self.end_headers()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port",    type=int, default=8765)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()

    # Init DB if it doesn't exist yet
    if not os.path.exists(DB_PATH):
        from aggregator import init_db
        conn = sqlite3.connect(DB_PATH)
        init_db(conn)
        conn.close()
        print("  Created new database. Run aggregator.py to populate it.")

    url = f"http://localhost:{args.port}"
    print(f"\n  Utah Mountain Living — Longitude Market Intelligence Curation Dashboard")
    print(f"  Open: {url}")
    print(f"  Stop: Ctrl+C\n")

    if not args.no_open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    server = HTTPServer(("localhost", args.port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server stopped.")


if __name__ == "__main__":
    main()
