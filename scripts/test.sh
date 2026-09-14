#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../backend"
. .venv/bin/activate
ruff check app tests
pytest -q "$@"
