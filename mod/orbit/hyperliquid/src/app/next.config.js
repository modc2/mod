/** @type {import('next').NextConfig} */
const API = process.env.HL_API_URL || "http://localhost:8919";
const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? "/hyperliquid";

module.exports = {
  reactStrictMode: true,
  ...(basePath ? { basePath } : {}),
  env: {
    NEXT_PUBLIC_BASE_PATH: basePath,
  },
  async rewrites() {
    return [
      {
        source: "/hl/:path*",
        destination: `${API}/:path*`,
        ...(basePath ? { basePath: false } : {}),
      },
      {
        source: `/api${basePath}/:path*`,
        destination: `${API}/:path*`,
        basePath: false,
      },
    ];
  },
};
