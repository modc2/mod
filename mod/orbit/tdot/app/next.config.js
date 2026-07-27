const fs = require('fs')
const path = require('path')

let apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:50320'
try {
  const config = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'config.json'), 'utf-8'))
  if (config.urls?.api) apiUrl = config.urls.api
} catch {}

/** @type {import('next').NextConfig} */
const nextConfig = {
  basePath: '/tdot',
  reactStrictMode: true,
  env: {
    // Same-origin so the gateway can proxy the whole app under one route.
    NEXT_PUBLIC_API_URL: '/tdot/api',
  },
  async rewrites() {
    return [{ source: '/api/:path*', destination: `${apiUrl}/:path*` }]
  },
}

module.exports = nextConfig
