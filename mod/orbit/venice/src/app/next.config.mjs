// Backend (Rust gateway) URL — the dev-mode rewrite target when the Caddy
// gateway isn't in front of Next.js. In prod, Caddy routes /api/venice/* →
// venice-api directly.
const apiUrl = process.env.VENICE_API_URL || "http://localhost:50880";
// Served under modc2.com/venice via the gateway → app carries the base path.
const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? "/venice";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: false,
  ...(basePath ? { basePath } : {}),
  env: {
    NEXT_PUBLIC_API_URL: "/api/venice",
    NEXT_PUBLIC_BASE_PATH: basePath,
  },
  async rewrites() {
    return [
      // Client fetches /api/venice/* (at the domain root, NOT under basePath)
      // → proxy to the Rust gateway. basePath:false mirrors the Caddy block.
      { source: "/api/venice/:path*", destination: `${apiUrl}/:path*`, basePath: false },
    ];
  },
};

export default nextConfig;
