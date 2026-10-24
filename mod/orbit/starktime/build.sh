#!/usr/bin/env bash
# build.sh — build the starktime app (Next.js static export), the Rust API
# and the Cairo contract. The app is published atomically: the new export
# lands in releases/<ts> and the `dist` symlink flips in one mv, so an
# app-only change needs no restart. An API change needs `m starktime/serve`.
set -euo pipefail
cd "$(dirname "$0")"

echo "== app (next export) =="
(cd app && npm install --no-audit --no-fund && npm run build)

ts=$(date +%Y%m%d-%H%M%S)
mkdir -p releases
cp -r app/out "releases/$ts"
ln -sfn "releases/$ts" dist.tmp
mv -T dist.tmp dist
echo "app -> releases/$ts (dist updated)"
ls -1dt releases/*/ 2>/dev/null | tail -n +6 | xargs -r rm -rf

echo "== api (cargo release) =="
(cd api && cargo build --release)
echo "binary -> api/target/release/starktime-api"

SCARB="$(command -v scarb || echo "$HOME/.local/bin/scarb")"
if [ -x "$SCARB" ]; then
  echo "== contract (scarb) =="
  (cd contracts && "$SCARB" build)
else
  echo "scarb not installed — skipping Cairo build"
fi
