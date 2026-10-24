#!/usr/bin/env bash
# Build the console and swap it in atomically: next build -> out/ ->
# releases/<ts>/ -> dist symlink (server.py serves dist/). Keeps 3 releases.
set -euo pipefail
cd "$(dirname "$0")"
[ -d node_modules/next ] || npm install --no-audit --no-fund
exec 9>/tmp/ztensor-app-build.lock
flock 9
rm -rf out
npx next build
ts=$(date +%Y%m%d-%H%M%S)
mkdir -p releases
cp -a out "releases/$ts"
ln -sfn "releases/$ts" dist.tmp && mv -Tf dist.tmp dist
ls -1dt releases/* | tail -n +4 | xargs -r rm -rf
echo "console -> $(readlink dist)"
