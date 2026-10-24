/* ztensor console — a static Next.js export served by the Rust API itself.
 *
 * No node process at runtime: `npm run build` writes out/, build.sh swaps it
 * into ../dist atomically, and ztensor-api on :51180 serves dist/ next to
 * the JSON API. basePath is /ztensor because the gateway keeps the prefix
 * (modc2.com/ztensor); the server normalizes it away, so the same build
 * also works on the bare port.
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
