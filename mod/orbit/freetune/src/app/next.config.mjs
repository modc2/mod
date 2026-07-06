const apiUrl = process.env.FREETUNE_API_URL || "http://localhost:50210";
const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? "/freetune";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: false,
  ...(basePath ? { basePath } : {}),
  env: {
    NEXT_PUBLIC_API_URL: "/api/freetune",
    NEXT_PUBLIC_BASE_PATH: basePath,
  },
  async rewrites() {
    // Dev/standalone fallback so the app works without the Caddy gateway in front.
    return [
      {
        source: "/api/freetune/:path*",
        destination: `${apiUrl}/:path*`,
        basePath: false,
      },
    ];
  },
};
export default nextConfig;
