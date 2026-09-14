#!/usr/bin/env bash
# Start the backend (and the Vite dev server if the frontend is installed).
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -d backend/.venv ]; then
  echo "No virtualenv found. Run ./scripts/setup.sh first."
  exit 1
fi

cleanup() { jobs -p | xargs -r kill 2>/dev/null || true; }
trap cleanup EXIT

(
  cd backend
  . .venv/bin/activate
  exec uvicorn app.main:app --reload --host 127.0.0.1 --port "${DEALBENCH_PORT:-8756}"
) &

if [ -d frontend/node_modules ]; then
  ( cd frontend && exec npm run dev ) &
  echo
  echo "  API  http://127.0.0.1:${DEALBENCH_PORT:-8756}/docs"
  echo "  UI   http://127.0.0.1:5173"
  echo
else
  echo
  echo "  API  http://127.0.0.1:${DEALBENCH_PORT:-8756}/docs"
  echo "  (frontend not installed — run ./scripts/setup.sh)"
  echo
fi

wait
