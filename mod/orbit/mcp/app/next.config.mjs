/** @type {import('next').NextConfig} */
// Backend (FastAPI hub) URL — only used as the dev-mode rewrite target when the
// Caddy gateway isn't in front of Next.js. In prod, Caddy routes
// /api/mcp/* → mcp-api directly (orbit/caddy generates that block).
const apiUrl = process.env.MCP_API_URL || "http://localhost:50360";
// Served under {host}/mcp via the gateway → the app must carry the base path.
const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? "/mcp";

const nextConfig = {
  ...(basePath ? { basePath } : {}),
  env: {
    NEXT_PUBLIC_API_URL: "/api/mcp",
    NEXT_PUBLIC_BASE_PATH: basePath,
  },
  async rewrites() {
    return [
      // Dev fallback: the client fetches /api/mcp/* at the domain root (NOT
      // under basePath) → proxy to the hub API. basePath:false keeps the source
      // at root so it mirrors the Caddy @mcp_api block.
      { source: "/api/mcp/:path*", destination: `${apiUrl}/:path*`, basePath: false },
    ];
  },
};

export default nextConfig;
