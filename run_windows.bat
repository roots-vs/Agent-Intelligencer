@echo off
REM run_windows.bat — Wasatch Intelligence one-click launcher (Windows)
REM ─────────────────────────────────────────────────────────────────────
REM Fetches latest articles, then opens the curation dashboard.
REM Double-click this file to start.

cd /d "%~dp0"

echo.
echo   Wasatch Intelligence — Starting up...
echo.

where python >nul 2>&1
if %errorlevel% neq 0 (
    echo   Python is not installed.
    echo   Download it from https://www.python.org/downloads/
    pause
    exit /b 1
)

echo   Fetching RSS feeds...
python aggregator.py

echo.
echo   Opening dashboard at http://localhost:8765
echo   Press Ctrl+C to stop the server.
echo.
python server.py

pause
