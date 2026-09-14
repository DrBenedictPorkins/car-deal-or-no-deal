#!/usr/bin/env bash
# Load the Honda reference negotiation as sample data.
set -euo pipefail
cd "$(dirname "$0")/../backend"
. .venv/bin/activate
python -m app.cli seed "$@"
python -m app.cli report
