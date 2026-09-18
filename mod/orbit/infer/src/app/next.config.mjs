// The gateway keeps the /infer prefix on app routes (app_port), so the app
// lives under basePath /infer. The REST API stays on the python server
// (:50820); /infer/_api/* is proxied there so the page works both through the
// gateway and bare on the app port.
const API = process.env.INFER_API || 'http://127.0.0.1:50820';

/** @type {import('next').NextConfig} */
const nextConfig = {
  basePath: '/infer',
  // Lets a dev server run against a separate dist dir without clobbering the
  // live build: NEXT_DIST_DIR=.next-devcheck npx next dev -p <free port>
  distDir: process.env.NEXT_DIST_DIR || '.next',
  async rewrites() {
    return [
      { source: '/_api/:path*', destination: `${API}/infer/_api/:path*` },
    ];
  },
};

export default nextConfig;
