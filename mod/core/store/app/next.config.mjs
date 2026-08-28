/** @type {import('next').NextConfig} */
// Backend (FastAPI gateway) URL — used only as the dev-mode rewrite target when
// the Caddy gateway isn't in front of Next.js. In prod, Caddy routes
// /api/store/* → store-api directly (see /etc/caddy/Caddyfile).
const apiUrl = process.env.STORE_API_URL || "http://localhost:50152";
// Served under modc2.com/store via the gateway → app must carry the base path.
const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? "/store";

const nextConfig = {
  ...(basePath ? { basePath } : {}),
  env: {
    NEXT_PUBLIC_API_URL: "/api/store",
    NEXT_PUBLIC_BASE_PATH: basePath,
  },
  async rewrites() {
    return [
      // Dev fallback: client fetches /api/store/* (at the domain root, NOT under
      // basePath) → proxy to the FastAPI gateway. basePath:false keeps the source
      // at root so it mirrors the Caddy @store_api block.
      { source: "/api/store/:path*", destination: `${apiUrl}/:path*`, basePath: false },
    ];
  },
};

export default nextConfig;
