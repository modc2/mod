const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:50150";
const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? "/copytensor";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: false,
  ...(basePath ? { basePath } : {}),
  env: {
    NEXT_PUBLIC_API_URL: "/api/copytensor",
    NEXT_PUBLIC_BASE_PATH: basePath,
  },
  async rewrites() {
    return [
      {
        source: "/api/copytensor/:path*",
        destination: `${apiUrl}/:path*`,
        basePath: false,
      },
    ];
  },
};

module.exports = nextConfig;
