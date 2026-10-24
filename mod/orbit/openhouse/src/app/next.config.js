const fs = require('fs')
const path = require('path')

let apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:50132'
for (const rel of [['..', '..', 'config.json'], ['..', 'config.json']]) {
  try {
    const config = JSON.parse(fs.readFileSync(path.join(__dirname, ...rel), 'utf-8'))
    if (config.urls?.api) apiUrl = config.urls.api
    break
  } catch {}
}

/** @type {import('next').NextConfig} */
const nextConfig = {
  basePath: '/openhouse',
  output: 'standalone',
  reactStrictMode: true,
  swcMinify: true,
  env: {
    NEXT_PUBLIC_API_URL: '/openhouse/api',
  },
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: `${apiUrl}/:path*`,
      },
    ];
  },
  webpack: (config) => {
    config.resolve.fallback = { fs: false, net: false, tls: false };
    return config;
  },
}

module.exports = nextConfig
