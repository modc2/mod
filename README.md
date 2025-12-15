# Uniswap V3/V4 DEX on Base Network

A fully functional decentralized exchange built with Next.js, integrating Uniswap V3 and V4 protocols on Base network with MetaMask support.

## Features

- 🔄 Swap tokens using Uniswap V3 on Base
- 🦊 MetaMask integration via RainbowKit
- 🎨 Modern UI with Tailwind CSS
- 🐳 Docker containerized for easy deployment
- ⚡ Built with Next.js 14 and TypeScript

## Quick Start

### Using Docker Compose

```bash
# Clone and navigate to directory
cd your-project

# Update .env.local with your WalletConnect Project ID
# Get one at https://cloud.walletconnect.com/

# Build and run
docker-compose up --build
```

The app will be available at `http://localhost:3000`

### Local Development

```bash
# Install dependencies
npm install

# Run development server
npm run dev
```

## Configuration

1. Get a WalletConnect Project ID from https://cloud.walletconnect.com/
2. Update `.env.local`:
   ```
   NEXT_PUBLIC_WALLET_CONNECT_PROJECT_ID=your_project_id
   ```

## Network Details

- **Chain**: Base Mainnet (Chain ID: 8453)
- **RPC**: https://mainnet.base.org
- **Uniswap V3 Router**: 0x2626664c2603336E57B271c5C0b26F421741e481

## Supported Tokens

- WETH: 0x4200000000000000000000000000000000000006
- USDC: 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913

## Tech Stack

- Next.js 14
- TypeScript
- Wagmi & Viem
- RainbowKit
- Uniswap V3 SDK
- Tailwind CSS
- Docker

## Important Notes

⚠️ **Before using in production:**

1. Implement proper token approval flow
2. Add slippage protection
3. Implement price impact warnings
4. Add proper error handling
5. Test thoroughly on testnet
6. Audit smart contract interactions

## License

MIT