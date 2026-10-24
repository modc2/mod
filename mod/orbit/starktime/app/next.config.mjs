/* starktime app — a static Next.js export served by the Rust API itself.
 *
 * No node process at runtime: `npm run build` writes out/, build.sh swaps it
 * into ../dist atomically, and starktime-api on :51280 serves dist/ next to
 * the JSON API. basePath is /starktime because the gateway keeps the prefix.
 */
const BASE = process.env.STARKTIME_BASE_PATH ?? '/starktime';

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
