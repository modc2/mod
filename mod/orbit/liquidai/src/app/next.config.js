const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:50460";
const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? "/liquidai";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: false,
  // `next build` empties its dist dir first, so building into the directory
  // `next start` is serving from 400s every chunk for the length of the build
  // and leaves open tabs unhydrated. Builds go to .next-staging and get moved
  // into place — see build.sh.
  distDir: process.env.NEXT_DIST_DIR || ".next",
  ...(basePath ? { basePath } : {}),
  env: {
    NEXT_PUBLIC_API_URL: "/liquidai/api",
    NEXT_PUBLIC_BASE_PATH: basePath,
  },
  async rewrites() {
    return [
      {
        // Canonical fleet form; legacy /api/liquidai alias kept below.
        source: "/liquidai/api/:path*",
        destination: `${apiUrl}/:path*`,
        basePath: false,
      },
      {
        source: "/api/liquidai/:path*",
        destination: `${apiUrl}/:path*`,
        basePath: false,
      },
    ];
  },
};

module.exports = nextConfig;
