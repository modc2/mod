/* starknet app — a static Next.js export served by api/api.py itself.
 *
 * No node process at runtime: build.sh runs `next build` into out/ and swaps
 * it into dist/ atomically; the Python server on :51020 serves dist/ next to
 * the REST API and /mcp. basePath is /starknet because the gateway keeps the
 * prefix (modc2.com/starknet); the server strips it, so the same build works
 * on the bare port. In dev, /starknet/_api/* is proxied to the running API.
 */
const BASE = process.env.STARKNET_BASE_PATH ?? '/starknet';
const API = process.env.STARKNET_API ?? 'http://127.0.0.1:51020';
const dev = process.env.NODE_ENV === 'development';

export default {
  ...(dev ? {} : { output: 'export' }),
  basePath: BASE,
  trailingSlash: false,
  images: { unoptimized: true },
  distDir: process.env.NEXT_DIST_DIR || '.next',
  env: { NEXT_PUBLIC_BASE: BASE },
  reactStrictMode: true,
  poweredByHeader: false,
  ...(dev ? {
    async rewrites() {
      return [{ source: '/_api/:path*', destination: `${API}/:path*` }];
    },
  } : {}),
};
