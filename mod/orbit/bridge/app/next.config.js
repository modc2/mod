const fs = require('fs')
const path = require('path')

// Internal backend URL for Next.js rewrites (dev proxy to FastAPI)
// In Docker, set API_INTERNAL_URL=http://bridge-api:8840
let apiUrl = process.env.API_INTERNAL_URL || 'http://localhost:8840'
if (!process.env.API_INTERNAL_URL) {
  try {
    const config = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'config.json'), 'utf-8'))
    if (config.port) apiUrl = `http://localhost:${config.port}`
  } catch {}
}

// basePath only when proxied behind the main app (e.g. NEXT_PUBLIC_BASE_PATH=/bridge)
const basePath = process.env.NEXT_PUBLIC_BASE_PATH || '/bridge'

/** @type {import('next').NextConfig} */
const nextConfig = {
  ...(basePath ? { basePath } : {}),
  output: 'standalone',
  reactStrictMode: true,
  swcMinify: true,
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || 'https://modc2.com/api/bridge',
    NEXT_PUBLIC_APP_URL: process.env.NEXT_PUBLIC_APP_URL || 'https://modc2.com/bridge',
    NEXT_PUBLIC_BASE_PATH: basePath,
  },
  async rewrites() {
    return [
      {
        source: '/api/bridge/:path*',
        destination: `${apiUrl}/:path*`,
        ...(basePath ? { basePath: false } : {}),
      },
    ];
  },
  webpack: (config) => {
    config.resolve.fallback = { fs: false, net: false, tls: false };
    return config;
  },
}

module.exports = nextConfig
