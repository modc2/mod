#!/usr/bin/env bash
# build.sh — build the zQuorum console (Next.js static export) and publish it
# atomically: the new export lands in releases/<ts> and the `dist` symlink
# flips in one mv, so server.py starts serving it with no restart. The API is
# pure Python (mod.py + server.py) — nothing to compile there.
set -euo pipefail
cd "$(dirname "$0")"

echo "== app (next export) =="
(cd app && npm install --no-audit --no-fund && npm run build)

ts=$(date +%Y%m%d-%H%M%S)
mkdir -p releases
cp -r app/out "releases/$ts"
ln -sfn "releases/$ts" dist.tmp
mv -T dist.tmp dist
echo "console -> releases/$ts (dist updated)"

# keep the last 5 releases
ls -1dt releases/*/ 2>/dev/null | tail -n +6 | xargs -r rm -rf
echo "done. serve with: m zquorum/serve  (or python3 server.py)"
