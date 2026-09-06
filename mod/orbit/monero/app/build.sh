#!/bin/bash
# Build the monero app into a staging dir, then swap it in atomically.
#
# Never build in place: `next build` wipes the dist dir first, so a rebuild
# under a live `next start` 400s every /_next/static chunk for the whole build
# window -- pages loaded then never hydrate and every click is dead. Building
# into .next-staging and mv'ing keeps the running server on a complete tree
# until the instant the new one is ready.
#
# flock serializes concurrent builds (two restarts firing close together would
# otherwise have one's `rm -rf .next-staging` delete the other mid-write).
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

exec 9>"$DIR/.build.lock"
flock -w 600 9 || { echo "build.sh: timed out waiting for a concurrent build"; exit 1; }

[ -d node_modules ] || npm install --no-audit --no-fund

export NEXT_PUBLIC_BASE_PATH="${NEXT_PUBLIC_BASE_PATH:-/monero}"
export NEXT_DIST_DIR=".next-staging"

rm -rf "$DIR/.next-staging"
npx next build

# Carry the previous build's static chunks forward so tabs already open on the
# old hashes keep working instead of 404ing after the swap.
if [ -d "$DIR/.next/static" ]; then
  cp -rn "$DIR/.next/static/." "$DIR/.next-staging/static/" 2>/dev/null || true
fi

rm -rf "$DIR/.next-prev"
[ -d "$DIR/.next" ] && mv "$DIR/.next" "$DIR/.next-prev"
mv "$DIR/.next-staging" "$DIR/.next"
rm -rf "$DIR/.next-prev"

echo "build.sh: built $(cat "$DIR/.next/BUILD_ID" 2>/dev/null || echo '?')"
