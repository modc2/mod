const fs = require('fs')
const path = require('path')

let apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:50410'
try {
  const config = JSON.parse(fs.readFileSync(path.join(__dirname, '..', '..', 'config.json'), 'utf-8'))
  if (config.urls?.api) apiUrl = config.urls.api
} catch {}

/** @type {import('next').NextConfig} */
const nextConfig = {
  basePath: '/prefi',
  reactStrictMode: true,
  swcMinify: true,
  env: {
    // Same-origin so the gateway can proxy the whole app under one route.
    NEXT_PUBLIC_API_URL: '/prefi/api',
  },
  async rewrites() {
    // basePath is prepended automatically → /prefi/api/* proxies to the FastAPI.
    return [{ source: '/api/:path*', destination: `${apiUrl}/:path*` }]
  },
  webpack: (config) => {
    config.resolve.fallback = { fs: false, net: false, tls: false }
    return config
  },
}

module.exports = nextConfig
