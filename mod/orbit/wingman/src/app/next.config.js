/** @type {import('next').NextConfig} */
module.exports = {
  basePath: '/wingman',
  async rewrites() {
    return [
      {
        source: '/_api/:path*',
        destination: 'http://localhost:50830/:path*',
      },
    ]
  },
}
