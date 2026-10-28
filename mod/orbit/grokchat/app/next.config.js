/** @type {import('next').NextConfig} */
const API = process.env.GROKCHAT_API || 'http://localhost:50930'

module.exports = {
  basePath: '/grokchat',
  async rewrites() {
    // Same-origin API per fleet convention: /grokchat/_api/* → python API.
    return [{ source: '/_api/:path*', destination: `${API}/:path*` }]
  },
}
