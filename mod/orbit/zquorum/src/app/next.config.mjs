/* zQuorum console — a static Next.js export served by the module's own
 * Python server (server.py), no node process at runtime.
 *
 * `npm run build` writes out/, build.sh swaps it into ../dist atomically, and
 * the stdlib server on :51300 serves dist/ next to the JSON API. basePath is
 * /zquorum because the gateway keeps the app prefix (modc2.com/zquorum); the
 * server normalizes it away, so the same build also works on the bare port.
 */
const BASE = process.env.ZQUORUM_BASE_PATH ?? '/zquorum';

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
