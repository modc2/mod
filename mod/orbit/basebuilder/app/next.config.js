const basePath = process.env.NEXT_PUBLIC_BASE_PATH || '/basebuilder'
const apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:50200'

/** @type {import('next').NextConfig} */
const nextConfig = {
  basePath,
  env: {
    NEXT_PUBLIC_BASE_PATH: basePath,
    NEXT_PUBLIC_API_URL: apiUrl,
  },
  webpack: (config, { isServer }) => {
    if (!isServer) {
      config.resolve.fallback = { fs: false, net: false, tls: false };
    }
    return config;
  },
};
module.exports = nextConfig;
