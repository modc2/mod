const apiUrl = process.env.PRERANK_API_URL || "http://127.0.0.1:50630";
const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? "/prerank";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: false,
  // build.sh builds into a staging dir and swaps it in, so a rebuild under a
  // live `next start` never serves a half-written .next.
  distDir: process.env.NEXT_DIST_DIR || ".next",
  ...(basePath ? { basePath } : {}),
  env: {
    NEXT_PUBLIC_API_URL: `${basePath}/_api`,
    NEXT_PUBLIC_BASE_PATH: basePath,
  },
  async rewrites() {
    return [
      // Same-origin API. The console never talks to another port from the
      // browser, so it works behind the gateway and on localhost alike.
      { source: `${basePath}/_api/:path*`, destination: `${apiUrl}/:path*`, basePath: false },
    ];
  },
};
export default nextConfig;
