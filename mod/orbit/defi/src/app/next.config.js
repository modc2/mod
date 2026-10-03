/** @type {import('next').NextConfig} */
// basePath so the fleet gateway can serve this at /defi. NOT `output: standalone`
// — `next start` does not serve a standalone build, and the failure mode is a
// silently dead app rather than an error.
const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? "/defi";

module.exports = {
  basePath,
  assetPrefix: basePath,
  reactStrictMode: true,
  eslint: { ignoreDuringBuilds: true },
  env: {
    NEXT_PUBLIC_BASE_PATH: basePath,
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL ?? "",
  },
};
