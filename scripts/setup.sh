#!/usr/bin/env bash
# One-time setup: Python virtualenv, dependencies, frontend packages, database.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> Python environment"
cd backend
if command -v uv >/dev/null 2>&1; then
  uv venv .venv --python 3.11
  . .venv/bin/activate
  uv pip install -e ".[dev]"
else
  python3 -m venv .venv
  . .venv/bin/activate
  pip install -e ".[dev]"
fi

echo "==> Database"
alembic upgrade head
cd ..

if command -v npm >/dev/null 2>&1; then
  echo "==> Frontend"
  ( cd frontend && npm install )
else
  echo "==> npm not found, skipping frontend install"
fi

echo
echo "Done. Next:"
echo "  ./scripts/seed.sh    # load the reference negotiation (optional)"
echo "  ./scripts/dev.sh     # start the app"
