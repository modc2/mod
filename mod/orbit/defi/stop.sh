#!/usr/bin/env bash
# Stop both defi processes, however they were started.
set -uo pipefail

if command -v pm2 >/dev/null 2>&1; then
  pm2 delete defi-api defi-app 2>/dev/null || true
fi

for port in 50500 50501; do
  pid="$(lsof -ti :"$port" 2>/dev/null || true)"
  [ -n "$pid" ] && kill $pid 2>/dev/null || true
done

echo "defi stopped"
