#!/usr/bin/env bash
# Boot the Warm-Intro Pathfinder inside a Codespace: seed a writable warm graph
# from the bundled snapshot, then serve the app on the forwarded port (8000).
set -euo pipefail
cd "$(dirname "$0")/.."

# connect() writes to the graph as it searches, so run off a copy rather than
# the committed snapshot in resources/.
if [ ! -f graph.db ]; then
  # The bundled snapshot ships gzipped (resources/graph.db.gz) because the
  # enriched graph is >100MB raw, over GitHub's per-file cap. Decompress it into
  # the writable copy; fall back to a raw .db if a dev build left one.
  if [ -f resources/graph.db ]; then
    cp resources/graph.db graph.db
  else
    gunzip -c resources/graph.db.gz > graph.db
  fi
fi
export VCWI_DB_URL="sqlite:///./graph.db"
export VCWI_CACHE_DB="./cache.db"

exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
