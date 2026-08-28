#!/bin/bash
# Atomic production build for the Next app.
#
# `next build` writes straight into .next. Doing that under a live `next start`
# deletes the very chunks the running server is still handing out: every open
# tab — and anything that loads while the build runs — 404s its CSS and JS and
# paints as raw unstyled HTML. Build into a staging dir instead, carry the
# previous build's static assets forward so already-served pages keep
# resolving, then swap. .next is untouched until the swap, so the live server
# never serves a hole.
#
# flock serializes concurrent builds — sibling jobs restart the same module all
# the time, and two `next build`s sharing a staging dir corrupt each other.
set -euo pipefail
cd "$(dirname "$0")"

STAGE=.next.staging
PREV=.next.prev

exec 9>.next.build.lock
flock 9

rm -rf "$STAGE"
mkdir -p "$STAGE"
# Carry the webpack cache over, else every build is a cold full recompile.
# Hardlinks — cheap, and next only ever adds or replaces whole cache files.
if [ -d .next/cache ]; then cp -al .next/cache "$STAGE/cache" 2>/dev/null || true; fi

NEXT_PUBLIC_BASE_PATH="${NEXT_PUBLIC_BASE_PATH:-/build-fork}" \
NEXT_DIST_DIR="$STAGE" \
  npx next build

if [ ! -d "$STAGE/static" ]; then
  echo "✗ build produced no $STAGE/static — refusing to swap, leaving .next alone"
  exit 1
fi

# Deploy skew: a tab that loaded the OLD html still asks for the OLD chunk
# URLs. Those paths are content-hashed, so merging the previous build's static
# tree in (-n, new files win) keeps those requests answerable instead of
# 404ing them the moment we swap. -a preserves mtimes so the prune below can
# tell generations apart; anything older than two days is past any live tab.
if [ -d .next/static ]; then
  fresh=$(find "$STAGE/static" -type f | wc -l)
  cp -an .next/static/. "$STAGE/static/" 2>/dev/null || true
  find "$STAGE/static" -type f -mtime +2 -delete 2>/dev/null || true
  find "$STAGE/static" -type d -empty -delete 2>/dev/null || true
  echo "▸ static: $fresh new + $(( $(find "$STAGE/static" -type f | wc -l) - fresh )) carried forward"
fi

# -T so a surviving .next can never swallow the staging dir as a child instead
# of being replaced by it, and restore-on-failure so a half-done swap can't
# leave the server with no .next at all.
rm -rf "$PREV"
if [ -d .next ]; then mv -T .next "$PREV"; fi
if ! mv -T "$STAGE" .next; then
  echo "✗ swap failed — restoring the previous build"
  if [ -d "$PREV" ]; then mv -T "$PREV" .next; fi
  exit 1
fi
rm -rf "$PREV"

echo "✓ built into $STAGE and swapped into .next (old static carried forward)"
