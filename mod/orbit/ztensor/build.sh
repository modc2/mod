#!/usr/bin/env bash
# build.sh — build the ztensor console (Next.js static export) and the Rust
# API, then publish the console atomically: new export lands in releases/<ts>
# and the `dist` symlink flips in one mv. No server restart needed for a
# console-only change; an API change needs a binary restart (m ztensor/serve).
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

echo "== api (cargo release) =="
(cd api && cargo build --release)
echo "binary -> api/target/release/ztensor-api"
