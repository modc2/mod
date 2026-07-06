/** @type {import('next').NextConfig} */
const nextConfig = {
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: 'http://localhost:50088/:path*',
      },
    ];
  },
};

export default nextConfig;
