const apiUrl = process.env.POLYMARKET_API_URL || "http://localhost:50091";
const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? "/polymarket";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: false, // Prevent double-mount in dev that causes API race conditions
  // build.sh builds into a staging dir and atomically swaps it in, so the
  // live `next start` never serves from a half-written .next (in-place
  // rebuilds used to 400 every _next/static chunk for the whole build).
  distDir: process.env.NEXT_DIST_DIR || ".next",
  // instrumentation.ts — starts the hub's 2-hourly background backtest worker
  // when the server boots. Still experimental in Next 14; stable in 15.
  experimental: { instrumentationHook: true },
  ...(basePath ? { basePath } : {}),
  env: {
    NEXT_PUBLIC_API_URL: "/api/polymarket",
    NEXT_PUBLIC_BASE_PATH: basePath,
    NEXT_PUBLIC_STRAT_HMAC_SECRET: process.env.NEXT_PUBLIC_STRAT_HMAC_SECRET || "",
  },
  webpack: (config) => {
    // `import src from "./foo.ts?raw"` → bundles the file as a UTF-8 string.
    // Used by the STRATS tab source viewer to show the built-in strat code.
    config.module.rules.push({
      resourceQuery: /raw/,
      type: "asset/source",
    });
    return config;
  },
  async rewrites() {
    return [
      {
        source: "/api/polymarket/:path*",
        destination: `${apiUrl}/:path*`,
        basePath: false,
      },
      // L2 CLOB passthrough (order, balance-allowance, orders, cancel).
      // Mirrors the Caddy @l2 block so the app works in dev modes where
      // the docker-generated Caddyfile isn't in front of Next.js.
      {
        source: "/api/polymarket-l2/:path*",
        destination: "https://clob.polymarket.com/:path*",
        basePath: false,
      },
    ];
  },
};
export default nextConfig;
