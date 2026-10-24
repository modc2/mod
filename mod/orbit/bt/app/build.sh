#!/usr/bin/env bash
# Build the bt console and publish it without a gap.
#
#   next build  ->  out/        (static export, basePath /bt)
#   out/        ->  releases/<t>, dist -> releases/<t>   (atomic symlink swap)
#
# bt.server reads dist/ per request, so no restart is needed. If the build
# fails, the previous dist/ keeps serving — and if there has never been a
# dist/, bt.server falls back to legacy.html (the single-file console).
set -euo pipefail
cd "$(dirname "$0")"

[ -d node_modules/next ] || npm ci --no-audit --no-fund || npm install --no-audit --no-fund
npx tsc --noEmit -p tsconfig.json          # every type error at once, before next stops at the first
rm -rf out
npx next build
[ -f out/index.html ] || { echo "build produced no out/index.html" >&2; exit 1; }

# dist is a symlink to releases/<stamp>; swapping it is one rename(2)
stamp=$(date +%s)
mkdir -p releases
cp -r out "releases/$stamp"
ln -sfn "releases/$stamp" dist.tmp
[ -d dist ] && [ ! -L dist ] && rm -rf dist     # first run over a plain dir
mv -T dist.tmp dist
rm -rf out
# keep the last three releases for a quick roll back (ln -sfn releases/<old> dist)
ls -1 releases | sort -n | head -n -3 | while read -r old; do rm -rf "releases/$old"; done
echo "published $(find -L dist -type f | wc -l) files -> $(pwd)/dist"
