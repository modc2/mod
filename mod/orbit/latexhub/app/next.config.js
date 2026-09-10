const fs = require('fs')
const path = require('path')

// The API port comes from the module's own config.json so the two can't drift.
let apiUrl = process.env.API_INTERNAL_URL || 'http://localhost:50200'
if (!process.env.API_INTERNAL_URL) {
  try {
    const config = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'config.json'), 'utf-8'))
    if (config.port) apiUrl = `http://localhost:${config.port}`
  } catch {}
}

/** @type {import('next').NextConfig} */
const nextConfig = {
  basePath: '/latexhub',
  reactStrictMode: true,
  // The browser always calls the same-origin path /api/latexhub: through the
  // gateway caddy proxies it, and on :3200 direct the rewrite below does.
  // A hardcoded localhost:50200 only ever worked on this host.
  async rewrites() {
    return [
      {
        source: '/api/latexhub/:path*',
        destination: `${apiUrl}/:path*`,
        basePath: false,
      },
    ]
  },
}

module.exports = nextConfig
