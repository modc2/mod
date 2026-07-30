const basePath = process.env.NEXT_PUBLIC_BASE_PATH || '/dev'
const apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8870'

/** @type {import('next').NextConfig} */
const nextConfig = {
  basePath,
  // Rebuilding .next in place under a live `next start` serves half-written
  // chunks to open tabs. Point NEXT_DIST_DIR at a staging dir for the build,
  // then swap it in and restart — `next start` (env unset) reads ".next".
  distDir: process.env.NEXT_DIST_DIR || '.next',
  env: {
    NEXT_PUBLIC_BASE_PATH: basePath,
  },
  // This host runs the whole orbit; a build often lands while the load average
  // is in the hundreds, and Next's default 60s page-data budget then expires
  // before the worker gets scheduled ("Collecting page data … timing out",
  // which leaves a half-written .next behind). Five minutes is slack, not a
  // change in what gets built.
  staticPageGenerationTimeout: 300,
  webpack: (config, { dev }) => {
    config.resolve.fallback = { fs: false, net: false, tls: false };
    // globals.css uses color-mix(in srgb, …); Next's bundled postcss-scss/cssnano
    // CSS minimizer chokes on it ("Unknown word") and it's injected after this
    // hook, so it can't be filtered from optimization.minimizer. Disabling CSS
    // minification for prod builds is the working fix (JS stays code-split).
    if (!dev) {
      config.optimization.minimize = false;
    }
    return config;
  },
  async rewrites() {
    return [
      {
        source: `/api${basePath}/:path*`,
        destination: `${apiUrl}/:path*`,
        basePath: false,
      },
    ]
  },
};

module.exports = nextConfig;
