/* bt console — a static Next.js export served by bt.server itself.
 *
 * No node process at runtime: `npm run build` writes out/, build.sh swaps it
 * into dist/ atomically, and the FastAPI server on :50280 serves dist/ next
 * to /api and /mcp. basePath is /bt because the gateway keeps the prefix
 * (modc2.com/bt); bt.server strips it, so the same build also works on the
 * bare port.
 */
const BASE = process.env.BT_BASE_PATH ?? '/bt';

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
