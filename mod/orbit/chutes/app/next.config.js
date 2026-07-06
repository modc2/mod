const fs = require('fs')
const path = require('path')

let apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:50120'
try {
  const config = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'config.json')))
  if (config.urls?.api) apiUrl = config.urls.api
} catch {}

/** @type {import('next').NextConfig} */
const nextConfig = {
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
