/** @type {import('next').NextConfig} */
const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? '/agent'
// NEXT_PUBLIC_* is inlined at BUILD time — default prod builds to the gateway
// path so a plain `npm run build` never bakes a localhost URL into the bundle.
const apiUrl = process.env.NEXT_PUBLIC_API_URL
  ?? (process.env.NODE_ENV === 'development' ? 'http://localhost:50117' : '/api/agent')
const nextConfig = {
  output: 'standalone',
  reactStrictMode: true,
  swcMinify: true,
  ...(basePath ? { basePath } : {}),
  env: { NEXT_PUBLIC_BASE_PATH: basePath, NEXT_PUBLIC_API_URL: apiUrl },
  webpack: (config) => {
    config.resolve.fallback = { fs: false, net: false, tls: false };
    return config;
  },
}

module.exports = nextConfig
