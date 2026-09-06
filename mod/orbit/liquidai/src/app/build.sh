#!/usr/bin/env bash
# Build the console without taking the running one down.
#
# `next build` wipes its dist directory before it writes, so building straight
# into .next while `next start` serves from it means a minute of 400s on every
# /_next/static chunk — dead buttons on any tab that was already open. So: build
# into .next-staging, carry the old static chunks forward (a tab loaded before
# the swap still asks for them), then move it into place in one step.
set -euo pipefail
cd "$(dirname "$0")"

exec 9>.build.lock
flock -w 900 9 || { echo "another build holds the lock"; exit 1; }

rm -rf .next-staging
NEXT_DIST_ERR=0
NEXT_DIST_DIR=.next-staging npm run build || NEXT_DIST_ERR=$?
if [ "$NEXT_DIST_ERR" != "0" ]; then
  echo "build failed — the running console is untouched"
  exit "$NEXT_DIST_ERR"
fi

if [ -d .next/static ]; then
  cp -rn .next/static/. .next-staging/static/ 2>/dev/null || true
fi

rm -rf .next-old
[ -d .next ] && mv .next .next-old
mv .next-staging .next
rm -rf .next-old
echo "built and swapped in"
