#!/usr/bin/env bash
# SpaceStudio launcher: builds the dashboard if needed and starts the local server on http://127.0.0.1:8000
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT/backend"
if [ ! -x .venv/bin/python ]; then
  echo "==> Creating backend virtual environment"
  python3 -m venv .venv
  .venv/bin/pip install -q -e '.[dev]'
fi
if [ ! -f "$ROOT/frontend/dist/index.html" ]; then
  echo "==> Building the dashboard"
  (cd "$ROOT/frontend" && npm install --no-audit --no-fund && npm run build)
fi
export PATH="$ROOT/scripts:$PATH"
exec .venv/bin/spacestudio serve "$@"
