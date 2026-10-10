// The gateway serves the console at {host}/neartensor and keeps the prefix,
// so the app must live under that basePath. API calls go through /rpc (not
// /api — {host}/neartensor/api belongs to the protocol API router).
const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "/neartensor";
const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:50185";

/** @type {import('next').NextConfig} */
const nextConfig = {
  basePath,
  env: {
    NEXT_PUBLIC_BASE_PATH: basePath,
  },
  async rewrites() {
    return [
      {
        source: "/rpc/:path*",
        destination: `${apiUrl}/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
