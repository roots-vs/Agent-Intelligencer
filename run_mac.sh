#!/bin/bash
# run_mac.sh — Wasatch Intelligence one-click launcher (Mac)
# ────────────────────────────────────────────────────────────
# Fetches latest articles, then opens the curation dashboard.
# Double-click this file in Finder, or run: bash run_mac.sh

cd "$(dirname "$0")"

echo ""
echo "  Wasatch Intelligence — Starting up…"
echo ""

# Check for Python 3
if ! command -v python3 &> /dev/null; then
    echo "  Python 3 is not installed."
    echo "  Download it from https://www.python.org/downloads/"
    read -p "  Press Enter to exit."
    exit 1
fi

# Fetch latest articles
echo "  Fetching RSS feeds…"
python3 aggregator.py

# Start the dashboard server (opens browser automatically)
echo ""
echo "  Opening dashboard at http://localhost:8765"
echo "  Press Ctrl+C to stop the server."
echo ""
python3 server.py
