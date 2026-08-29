/** @type {import('next').NextConfig} */
const nextConfig = {
  basePath: '/compute',
  assetPrefix: '/compute',
  webpack: (config, { isServer }) => {
    if (!isServer) {
      config.resolve.fallback = { fs: false, net: false, tls: false };
    }
    return config;
  },
};
module.exports = nextConfig;
