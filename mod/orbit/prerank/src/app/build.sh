#!/bin/bash
# Atomic production build.
#
# `next build` wipes its dist dir before it starts, so building straight into
# a live `.next` leaves a window where every _next/static chunk 400s and open
# tabs never hydrate. Build into a staging dir, carry the previous static
# chunks over for tabs that are mid-session, then swap it in with one mv.
set -e
cd "$(dirname "$0")"

# Serialize builds — two restarts landing close together would otherwise
# delete each other's staging dir mid-write.
exec 9>.build.lock
flock -w 600 9

[ ! -d node_modules ] && npm install --no-audit --no-fund

rm -rf .next-staging
NEXT_DIST_DIR=.next-staging npx next build

if [ -d .next/static ]; then
  find .next/static -type f -mtime +3 -delete 2>/dev/null || true
  cp -an .next/static/. .next-staging/static/ 2>/dev/null || true
fi

rm -rf .next-prev
[ -d .next ] && mv .next .next-prev
mv .next-staging .next
rm -rf .next-prev
