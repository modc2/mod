const apiUrl = process.env.WHITEPAPER_API_URL || "http://localhost:50106";
const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? "/whitepaper";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: false,
  ...(basePath ? { basePath } : {}),
  env: {
    NEXT_PUBLIC_API_URL: "/api/whitepaper",
    NEXT_PUBLIC_BASE_PATH: basePath,
  },
  async rewrites() {
    return [
      {
        source: "/api/whitepaper/:path*",
        destination: `${apiUrl}/:path*`,
        basePath: false,
      },
    ];
  },
};
export default nextConfig;
