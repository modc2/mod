// Backend (Rust gateway) URL — the dev-mode rewrite target when the Caddy
// gateway isn't in front of Next.js. In prod, Caddy routes /api/dev/* →
// dev-api directly.
const apiUrl = process.env.DEV_API_URL || "http://localhost:8870";
// Served under modc2.com/dev via the gateway → app carries the base path.
const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? "/dev";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: false,
  ...(basePath ? { basePath } : {}),
  env: {
    NEXT_PUBLIC_API_URL: "/api/dev",
    NEXT_PUBLIC_BASE_PATH: basePath,
  },
  async rewrites() {
    return [
      // Client fetches /api/dev/* (at the domain root, NOT under basePath)
      // → proxy to the Rust gateway. basePath:false mirrors the Caddy block.
      { source: "/api/dev/:path*", destination: `${apiUrl}/:path*`, basePath: false },
    ];
  },
};

export default nextConfig;
