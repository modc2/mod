const apiUrl = process.env.MODEL_API_URL || "http://localhost:50110";
const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? "/model";
const gateAddress = process.env.NEXT_PUBLIC_MODEL_GATE_ADDRESS || "";
const chainId = process.env.NEXT_PUBLIC_MODEL_GATE_CHAIN_ID || "84532";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: false,
  ...(basePath ? { basePath } : {}),
  env: {
    NEXT_PUBLIC_API_URL: "/api/model",
    NEXT_PUBLIC_BASE_PATH: basePath,
    NEXT_PUBLIC_MODEL_GATE_ADDRESS: gateAddress,
    NEXT_PUBLIC_MODEL_GATE_CHAIN_ID: chainId,
  },
  async rewrites() {
    return [
      {
        source: "/api/model/:path*",
        destination: `${apiUrl}/:path*`,
        basePath: false,
      },
    ];
  },
};
export default nextConfig;
