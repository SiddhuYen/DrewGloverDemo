#!/usr/bin/env bash
# Boot the Warm-Intro Pathfinder inside a Codespace: seed a writable warm graph
# from the bundled snapshot, then serve the app on the forwarded port (8000).
set -euo pipefail
cd "$(dirname "$0")/.."

# connect() writes to the graph as it searches, so run off a copy rather than
# the committed snapshot in resources/.
if [ ! -f graph.db ]; then
  cp resources/graph.db graph.db
fi
export VCWI_DB_URL="sqlite:///./graph.db"
export VCWI_CACHE_DB="./cache.db"

exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
