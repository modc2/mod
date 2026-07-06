const basePath = process.env.NEXT_PUBLIC_BASE_PATH || '/routy'
const apiUrl = process.env.ROUTY_API_URL || 'http://localhost:3000'

/** @type {import('next').NextConfig} */
const nextConfig = {
  basePath,
  env: {
    NEXT_PUBLIC_BASE_PATH: basePath,
    NEXT_PUBLIC_ROUTY_API: apiUrl,
  },
  async rewrites() {
    return [
      {
        source: '/api/routy/:path*',
        destination: `${apiUrl}/_api/:path*`,
        basePath: false,
      },
    ]
  },
  webpack: (config, { isServer }) => {
    if (!isServer) {
      config.resolve.fallback = { fs: false, net: false, tls: false };
    }
    return config;
  },
};
module.exports = nextConfig;
