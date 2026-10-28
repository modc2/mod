/* ztensor console — a static Next.js export served by src/server.py.
 *
 * No node at runtime: build.sh runs `next build`, then swaps out/ into
 * ./dist atomically; server.py serves dist/ next to the JSON API. basePath
 * /ztensor because the gateway keeps the prefix (modc2.com/ztensor).
 */
const BASE = process.env.ZTENSOR_BASE_PATH ?? '/ztensor';

export default {
  output: 'export',
  basePath: BASE,
  trailingSlash: false,
  images: { unoptimized: true },
  distDir: process.env.NEXT_DIST_DIR || '.next',
  env: { NEXT_PUBLIC_BASE: BASE },
  reactStrictMode: true,
  poweredByHeader: false,
};
