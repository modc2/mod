#!/bin/bash
# Atomic production build.
#
# `next build` starts by wiping its dist dir, so building straight into the
# live `.next` while `next start` is serving from it leaves a ~60s window
# where every _next/static chunk request 400s — pages loaded in that window
# never hydrate (dead tabs/buttons), and the restart that follows strands
# already-open pages on chunk hashes that no longer exist.
#
# Instead: build into .next-staging (distDir override in next.config.mjs),
# carry the previous builds' static chunks into the new tree so pages that
# are still open across the deploy can keep lazy-loading, then swap the
# whole dir in one mv. The live server sees a consistent tree the entire
# time; the swap itself is sub-second.
set -e
cd "$(dirname "$0")"

# Serialize builds. The console auto-restarts this module after EVERY edit
# job that completes, so two restarts can land <60s apart — the second run's
# `rm -rf .next-staging` then deletes the first build mid-write and both exit
# non-zero ("prod build failed", old server kept). Under the lock the second
# build just waits its turn; both succeed and the last swap wins.
exec 9>.build.lock
flock -w 600 9

rm -rf .next-staging
NEXT_DIST_DIR=.next-staging npx next build

# Keep prior chunk generations (content-hashed, so no collisions) for open
# tabs, but prune anything older than 3 days so they don't accumulate.
if [ -d .next/static ]; then
  find .next/static -type f -mtime +3 -delete 2>/dev/null || true
  cp -an .next/static/. .next-staging/static/ 2>/dev/null || true
fi

rm -rf .next-prev
if [ -d .next ]; then mv .next .next-prev; fi
mv .next-staging .next
rm -rf .next-prev
