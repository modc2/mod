const fs = require('fs')
const path = require('path')

let apiUrl = process.env.API_INTERNAL_URL || 'http://localhost:5380'
if (!process.env.API_INTERNAL_URL) {
  try {
    const config = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'config.json'), 'utf-8'))
    if (config.port) apiUrl = `http://localhost:${config.port}`
  } catch {}
}

/** @type {import('next').NextConfig} */
const nextConfig = {
  basePath: '/dns',
  output: 'standalone',
  reactStrictMode: true,
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || apiUrl,
  },
  async rewrites() {
    return [
      {
        source: '/api/dns/:path*',
        destination: `${apiUrl}/:path*`,
        basePath: false,
      },
    ]
  },
  webpack: (config) => {
    config.resolve.fallback = { fs: false, net: false, tls: false }
    return config
  },
}

module.exports = nextConfig
