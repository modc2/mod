#!/usr/bin/env bash
# pm2 entrypoint for the agent Next app (standalone server on :3117, basePath /agent).
set -e
cd "$(dirname "$0")"
export PORT="${PORT:-3117}"
export HOSTNAME="0.0.0.0"
export NEXT_PUBLIC_BASE_PATH="/agent"
export NEXT_PUBLIC_API_URL="/api/agent"
# Self-heal: a wiped, half-finished, or dev-clobbered .next would otherwise
# crash-loop pm2 — or, worse, serve HTML whose chunks 500, which looks like a
# styleless page rather than a broken build. Standalone builds don't include
# static assets, so the copy is part of "is this build coherent?": server.js
# present, its static tree present, and both halves on the same BUILD_ID.
# The copy itself lives in package.json's `postbuild`, so it also runs for the
# rebuilds other modules trigger — `next build` deletes standalone/.next/static
# outright, and the live server 404s every asset until something puts it back.
coherent() {
  [ -f .next/standalone/server.js ] || return 1
  [ -d .next/standalone/.next/static/chunks ] || return 1
  [ -f .next/BUILD_ID ] && [ -f .next/standalone/.next/BUILD_ID ] || return 1
  [ "$(cat .next/BUILD_ID)" = "$(cat .next/standalone/.next/BUILD_ID)" ]
}
if ! coherent; then
  rm -rf .next
  npm run build
fi
exec node .next/standalone/server.js
