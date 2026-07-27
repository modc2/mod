const basePath = process.env.NEXT_PUBLIC_BASE_PATH || '/dev'
const apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8870'

/** @type {import('next').NextConfig} */
const nextConfig = {
  basePath,
  env: {
    NEXT_PUBLIC_BASE_PATH: basePath,
  },
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
