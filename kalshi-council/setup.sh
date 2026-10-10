#!/usr/bin/env bash
# One-shot local setup for macOS and Linux: virtualenv, install, .env.
#   ./setup.sh          set up, then print how to start
#   ./setup.sh --start  set up and start the dashboard
set -euo pipefail
cd "$(dirname "$0")"

PY=""
for c in python3.13 python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1 &&
     "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Kalshi Council needs Python 3.11 or newer."
  echo "  macOS:  brew install python@3.12   (or https://www.python.org/downloads/)"
  echo "  Ubuntu: sudo apt install python3.12 python3.12-venv"
  exit 1
fi

echo "Using $("$PY" --version) at $(command -v "$PY")"
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -e ".[agents]"
[ -f .env ] || cp .env.example .env

DIR="$(pwd)"
cat <<EOF

Installed. Paper trading is the default; nothing touches real money until you change .env.

Start the dashboard:
  cd "$DIR" && .venv/bin/kalshi-council run
  then open http://127.0.0.1:8787

Add an agent to Claude Code (run while the dashboard is up):
  claude mcp add council-line-shark -- "$DIR/.venv/bin/kalshi-council" mcp --agent line-shark --name "Line Shark"

Run the built-in Claude crew (uses API credits):
  export ANTHROPIC_API_KEY=...   # or: ant auth login
  cd "$DIR" && .venv/bin/kalshi-council run --crew dot-plot

Backtest:
  "$DIR/.venv/bin/kalshi-council" backtest --asset DOGE --markets 60
EOF

if [ "${1:-}" = "--start" ]; then
  echo
  exec .venv/bin/kalshi-council run
fi
