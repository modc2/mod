// mod-web — the Next.js front door to the mod protocol.
//
// Backend (Rust mod-api) URL — the dev-mode rewrite target when the Caddy
// gateway isn't in front of Next.js. In prod, Caddy routes /api/web/* →
// mod-api directly.
const apiUrl = process.env.MOD_API_URL || "http://localhost:50420";
// The chain module's hub API — proxied server-side so the browser can drive
// on-chain registration / mint / pool without the hub being publicly routed.
const chainUrl = process.env.CHAIN_API_URL || "http://localhost:8800";
// Served under modc2.com/web via the gateway → app carries the base path.
const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? "/web";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  ...(basePath ? { basePath } : {}),
  env: {
    NEXT_PUBLIC_API_URL: "/api/web",
    NEXT_PUBLIC_BASE_PATH: basePath,
  },
  async rewrites() {
    return [
      // Client fetches /api/web/* (at the domain root, NOT under basePath) →
      // proxy to the Rust gateway. basePath:false mirrors the Caddy block.
      { source: "/api/web/:path*", destination: `${apiUrl}/:path*`, basePath: false },
      // Client fetches {basePath}/api/chain/* → proxy to the chain hub
      // (registration, MOD mint, reward pool, per-mod staking). Lives UNDER the
      // basePath so the Caddy gateway's existing /web/* route carries it in
      // prod — a domain-root /api/chain would never reach this app through the
      // gateway. The hub itself stays private; only this proxy is public.
      { source: "/api/chain/:path*", destination: `${chainUrl}/:path*` },
      // Back-compat for direct local use without the basePath.
      { source: "/api/chain/:path*", destination: `${chainUrl}/:path*`, basePath: false },
    ];
  },
};

export default nextConfig;
