const apiUrl = process.env.FREETUNE_API_URL || "http://localhost:50210";
const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? "/freetune";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: false,
  ...(basePath ? { basePath } : {}),
  env: {
    NEXT_PUBLIC_API_URL: "/freetune/api",
    NEXT_PUBLIC_BASE_PATH: basePath,
  },
  async rewrites() {
    // Dev/standalone fallback so the app works without the Caddy gateway in front.
    return [
      {
        // Canonical fleet form; legacy /api/freetune alias kept below.
        source: "/freetune/api/:path*",
        destination: `${apiUrl}/:path*`,
        basePath: false,
      },
      {
        source: "/api/freetune/:path*",
        destination: `${apiUrl}/:path*`,
        basePath: false,
      },
    ];
  },
};
export default nextConfig;
