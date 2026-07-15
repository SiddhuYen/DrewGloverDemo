#!/usr/bin/env bash
# Build the downloadable Mac app (.app + .dmg) locally. Run from anywhere.
set -euo pipefail
cd "$(dirname "$0")/.."

# refresh the bundled graph snapshot from the live DB, if present
[ -f vcwarmintro.db ] && cp vcwarmintro.db resources/graph.db

python -m spacy download en_core_web_sm >/dev/null 2>&1 || true
pip install -q pywebview platformdirs pyinstaller zipp httpx

rm -rf dist build/work
pyinstaller build/pathfinder.spec --noconfirm --distpath dist --workpath build/work
hdiutil create -volname "Warm Intro Pathfinder" \
  -srcfolder "dist/WarmIntroPathfinder.app" -ov -format UDZO \
  "dist/WarmIntroPathfinder.dmg"

echo "Built:"
echo "  dist/WarmIntroPathfinder.app  (the app)"
echo "  dist/WarmIntroPathfinder.dmg  (downloadable installer)"
