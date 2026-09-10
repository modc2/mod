const fs = require('fs')
const path = require('path')

let apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:50310'
try {
  const config = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'config.json'), 'utf-8'))
  if (config.urls?.api) apiUrl = config.urls.api
} catch {}

/**
 * Where the build lands. `next start` is serving `.next` out of this same
 * directory while a rebuild runs, and overwriting it underneath a live server
 * hands every open tab a chunk that no longer exists. So a deploy builds into
 * a scratch dist (`NYC_DIST_DIR=.next-build npm run build`), then swaps it into
 * place and restarts — the running server never sees a half-written build.
 * Unset, which is how pm2 runs it, this is the ordinary `.next`.
 */
const distDir = process.env.NYC_DIST_DIR || '.next'

/** @type {import('next').NextConfig} */
const nextConfig = {
  basePath: '/nyc',
  reactStrictMode: true,
  distDir,
  env: {
    // Same-origin so the gateway can proxy the whole app under one route.
    NEXT_PUBLIC_API_URL: '/nyc/api',
  },
  async rewrites() {
    return [{ source: '/api/:path*', destination: `${apiUrl}/:path*` }]
  },
}

module.exports = nextConfig
