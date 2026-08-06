const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:50460";
const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? "/liquidai";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: false,
  ...(basePath ? { basePath } : {}),
  env: {
    NEXT_PUBLIC_API_URL: "/api/liquidai",
    NEXT_PUBLIC_BASE_PATH: basePath,
  },
  async rewrites() {
    return [
      {
        source: "/api/liquidai/:path*",
        destination: `${apiUrl}/:path*`,
        basePath: false,
      },
    ];
  },
};

module.exports = nextConfig;
