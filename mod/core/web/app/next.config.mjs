// mod-web — the Next.js front door to the mod protocol.
//
// Backend (Rust mod-api) URL — the dev-mode rewrite target when the Caddy
// gateway isn't in front of Next.js. In prod, Caddy routes /api/web/* →
// mod-api directly.
const apiUrl = process.env.MOD_API_URL || "http://localhost:50420";
// The chain module's hub API — proxied server-side so the browser can drive
// on-chain registration / mint / pool without the hub being publicly routed.
const chainUrl = process.env.CHAIN_API_URL || "http://localhost:8800";
// The bloctime module's API — the BlocTime protocol itself (contracts, curve
// params, positions). Reads only; every write is signed by the visitor's
// wallet in the browser, so no key ever crosses this proxy.
const bloctimeUrl = process.env.BLOCTIME_API_URL || "http://localhost:8851";
// Served under modc2.com/web via the gateway → app carries the base path.
const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? "/web";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Build out-of-tree (NEXT_DIST_DIR=.next-stage) and swap the directory in
  // afterwards — building into .next under a live `next start` serves dead
  // chunks to anyone mid-session.
  ...(process.env.NEXT_DIST_DIR ? { distDir: process.env.NEXT_DIST_DIR } : {}),
  ...(basePath ? { basePath } : {}),
  env: {
    NEXT_PUBLIC_API_URL: "/web/api",
    NEXT_PUBLIC_BASE_PATH: basePath,
  },
  async rewrites() {
    return [
      // Client fetches /web/api/* (canonical; at the domain root, NOT under
      // basePath) → proxy to the Rust gateway. basePath:false mirrors the
      // Caddy block. The legacy /api/web alias stays supported below.
      { source: "/web/api/:path*", destination: `${apiUrl}/:path*`, basePath: false },
      { source: "/api/web/:path*", destination: `${apiUrl}/:path*`, basePath: false },
      // Client fetches {basePath}/_api/chain/* → proxy to the chain hub
      // (registration, MOD mint, reward pool, per-mod staking). Lives UNDER the
      // basePath so the Caddy gateway's existing /web/* route carries it in
      // prod. _api (not api) because /web/api/* now belongs to the protocol
      // API — Caddy hands it to mod-api before this app ever sees it; _api is
      // the fleet convention for an app's own internal routes. The hub itself
      // stays private; only this proxy is public.
      { source: "/_api/chain/:path*", destination: `${chainUrl}/:path*` },
      // Back-compat for direct local use without the basePath, plus the old
      // /api/chain shape for anything still holding a pre-flip bundle in dev.
      { source: "/_api/chain/:path*", destination: `${chainUrl}/:path*`, basePath: false },
      { source: "/api/chain/:path*", destination: `${chainUrl}/:path*` },
      { source: "/api/chain/:path*", destination: `${chainUrl}/:path*`, basePath: false },
      // Same deal for the bloctime module: under the basePath so the gateway's
      // /web/* route carries it, plus root + legacy aliases for local use.
      { source: "/_api/bloctime/:path*", destination: `${bloctimeUrl}/:path*` },
      { source: "/_api/bloctime/:path*", destination: `${bloctimeUrl}/:path*`, basePath: false },
      { source: "/api/bloctime/:path*", destination: `${bloctimeUrl}/:path*` },
      { source: "/api/bloctime/:path*", destination: `${bloctimeUrl}/:path*`, basePath: false },
    ];
  },
};

export default nextConfig;
