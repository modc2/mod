#!/usr/bin/env bash
# build.sh — build the zkworld console (Next.js static export) and publish it
# atomically: the export lands in src/releases/<ts> and the src/dist symlink
# flips in one mv, so the running server picks it up with no restart. The API
# is pure Python (src/mod.py + src/api/server.py) — nothing to compile.
set -euo pipefail
cd "$(dirname "$0")"

echo "== app (next export) =="
(cd app && npm install --no-audit --no-fund && npm run build)

ts=$(date +%Y%m%d-%H%M%S)
mkdir -p releases
cp -r app/out "releases/$ts"
ln -sfn "releases/$ts" dist.tmp
mv -T dist.tmp dist
echo "console -> src/releases/$ts (src/dist updated)"

# keep the last 5 releases
ls -1dt releases/*/ 2>/dev/null | tail -n +6 | xargs -r rm -rf
echo "done. serve with: m zkworld/serve  (or python3 src/api/server.py)"
