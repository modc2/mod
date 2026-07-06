const basePath = process.env.NEXT_PUBLIC_BASE_PATH || '/claude'
const apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8820'

/** @type {import('next').NextConfig} */
const nextConfig = {
  basePath,
  env: {
    NEXT_PUBLIC_BASE_PATH: basePath,
  },
  webpack: (config) => {
    config.resolve.fallback = { fs: false, net: false, tls: false };
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
