const fs = require('fs')
const path = require('path')

// The module's config.json is the single source of truth for the API port
// and the demo pool address — read once at server start.
let apiUrl = process.env.NEXT_PUBLIC_API_URL_ORIGIN || 'http://localhost:50850'
let demoPool = ''
try {
  const config = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'config.json'), 'utf-8'))
  if (config.urls?.api) apiUrl = config.urls.api
  demoPool = config.contracts?.local?.contracts?.TravisCountyHealthMutual?.address || ''
} catch {}

/** @type {import('next').NextConfig} */
const nextConfig = {
  basePath: '/selfinsure',
  reactStrictMode: true,
  env: {
    NEXT_PUBLIC_API_URL: '/selfinsure/api',
    NEXT_PUBLIC_DEMO_POOL: demoPool,
  },
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: `${apiUrl}/:path*`,
      },
    ]
  },
}

module.exports = nextConfig
