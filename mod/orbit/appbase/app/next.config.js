const basePath = process.env.NEXT_PUBLIC_BASE_PATH || '/appbase'

/** @type {import('next').NextConfig} */
const nextConfig = {
  basePath,
  webpack: (config, { isServer }) => {
    if (!isServer) {
      config.resolve.fallback = { fs: false, net: false, tls: false };
    }
    return config;
  },
};
module.exports = nextConfig;
