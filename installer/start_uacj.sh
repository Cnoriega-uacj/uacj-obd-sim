#!/usr/bin/env bash
# UACJ OBD-II Simulator — one-click laptop launcher (macOS / Linux)

set -e
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
    echo "[setup] First run detected. Creating Python virtualenv..."
    if ! command -v python3 >/dev/null 2>&1; then
        echo "python3 is not installed. Install Python 3.11+ and rerun." >&2
        exit 1
    fi
    python3 -m venv .venv
    echo "[setup] Installing UACJ simulator package and dependencies..."
    .venv/bin/pip install --upgrade pip
    .venv/bin/pip install -e .
    echo "[setup] Pre-loading sample vehicle sessions..."
    .venv/bin/python scripts/seed_sample_sessions.py data
fi

# Open the dashboard in the default browser after a short delay.
(
    sleep 3
    if command -v xdg-open >/dev/null 2>&1; then
        xdg-open http://localhost:8000
    elif command -v open >/dev/null 2>&1; then
        open http://localhost:8000
    fi
) &

cat <<EOF

============================================================
 UACJ OBD-II Training Simulator
 Dashboard: http://localhost:8000
 Press Ctrl+C to stop the server.
============================================================

EOF

exec .venv/bin/uacj-obd --data data serve --host 0.0.0.0 --port 8000
