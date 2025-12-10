# ModFi - Lending Protocol Aggregator on Base

## Overview
ModFi is a DeFi aggregator that connects users to multiple lending protocols (Aave, Compound) on Base mainnet to generate APR on USDC, USDT, and ETH.

## Features
- 🔗 Connect to multiple lending protocols
- 💰 Support for USDC, USDT, and ETH
- 🌐 Base mainnet integration
- 🎨 Modern Next.js frontend with RainbowKit
- 🐳 Docker & Docker Compose ready

## Quick Start

### Prerequisites
- Docker & Docker Compose
- Node.js 18+ (for local development)

### Deploy with Docker

1. Clone and navigate to the project:
```bash
cd /Users/homie/mod/mod/_mods/modfi
```

2. Create `.env` file:
```bash
cp .env.example .env
# Edit .env with your values
```

3. Build and run:
```bash
docker-compose up -d --build
```

4. Access the app:
```
http://localhost:3000
```

### Local Development

1. Install dependencies:
```bash
npm install
```

2. Run development server:
```bash
npm run dev
```

## Smart Contract Deployment

1. Deploy `contracts/LendingAggregator.sol` to Base mainnet
2. Update contract addresses in:
   - `src/app/page.tsx` (CONTRACT_ADDRESS)
   - `.env` (NEXT_PUBLIC_CONTRACT_ADDRESS)

## Protocol Integration

Update protocol addresses in the smart contract constructor:
- Aave V3 Pool on Base
- Compound on Base

## Tech Stack
- **Frontend**: Next.js 14, React, TypeScript
- **Web3**: Wagmi, Viem, RainbowKit
- **Styling**: Tailwind CSS
- **Smart Contracts**: Solidity 0.8.20
- **Deployment**: Docker, Docker Compose

## Commands

```bash
# Development
npm run dev

# Build
npm run build

# Start production
npm start

# Docker
docker-compose up -d        # Start
docker-compose down         # Stop
docker-compose logs -f      # View logs
```

## Security
- Smart contracts use OpenZeppelin libraries
- ReentrancyGuard protection
- Ownable access control

## License
MIT