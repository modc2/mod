/** @type {import('next').NextConfig} */

// The module is served either standalone on its own port or behind the fleet
// gateway under /zcash. basePath keeps assets correct in both cases, so the
// browser always calls `${basePath}/api/<fn>`; src/app/api/[fn]/route.ts serves
// that and forwards to the REST backend (ZCASH_API_ORIGIN, :8930 by default).
// It is a route handler rather than a rewrite because it also starts the
// backend when it is not running -- a rewrite to a dead port only ever 500s.
const basePath = process.env.NEXT_PUBLIC_BASE_PATH || ''

const nextConfig = {
  basePath: basePath || undefined,
  // build.sh builds into .next-staging and swaps it in, so a rebuild never
  // empties the dist dir the running server is serving from.
  distDir: process.env.NEXT_DIST_DIR || '.next',
  env: {
    NEXT_PUBLIC_BASE_PATH: basePath,
  },
}

module.exports = nextConfig
