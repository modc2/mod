/* cairo console — a static Next.js export served by the module's own stdlib
 * server (src/api/server.py), no node process at runtime.
 *
 * `npm run build` writes out/, ../build.sh swaps it into ../dist atomically.
 * basePath is /cairo because the gateway keeps the app prefix; the server
 * normalizes it away, so the same build also works on the bare port.
 */
const BASE = process.env.CAIRO_BASE_PATH ?? '/cairo';

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
