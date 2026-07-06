# Prefi Next.js App 🚀

Fully-fledged Next.js application for interacting with Prefi prediction market contracts.

## Features

- 🔌 **Multi-Network Support**: Ganache, Base, Base Sepolia
- 💼 **Wallet Integration**: RainbowKit + Wagmi
- 📊 **Place Predictions**: Predict asset prices with collateral
- 📈 **View Predictions**: Track your prediction history
- 🎨 **Modern UI**: Tailwind CSS with dark theme
- 🐳 **Docker Ready**: Containerized deployment

## Quick Start

### Development

```bash
# Install dependencies
npm install

# Copy environment variables
cp .env.example .env

# Edit .env with your contract addresses
# NEXT_PUBLIC_PREDICTION_MARKET_ADDRESS=0x...
# NEXT_PUBLIC_ORACLE_ADDRESS=0x...

# Run development server
npm run dev
```

Open [http://localhost:3000](http://localhost:3000)

### Docker Deployment

```bash
# Build and run with docker-compose
docker-compose up -d

# View logs
docker-compose logs -f

# Stop
docker-compose down
```

### Production Build

```bash
# Build for production
npm run build

# Start production server
npm start
```

## Network Configuration

The app supports three networks:

1. **Ganache (Local)**: Chain ID 1337
2. **Base Mainnet**: Chain ID 8453
3. **Base Sepolia**: Chain ID 84532

Switch networks using the network selector in the header.

## Environment Variables

```env
NEXT_PUBLIC_GANACHE_RPC=http://localhost:7545
NEXT_PUBLIC_BASE_RPC=https://mainnet.base.org
NEXT_PUBLIC_BASE_SEPOLIA_RPC=https://sepolia.base.org
NEXT_PUBLIC_CHAIN_ID=1337
NEXT_PUBLIC_PREDICTION_MARKET_ADDRESS=0x...
NEXT_PUBLIC_ORACLE_ADDRESS=0x...
NEXT_PUBLIC_WALLETCONNECT_PROJECT_ID=your_project_id
```

## Docker Network

The app connects to the `prefi_default` network to communicate with deployed contracts.

Ensure your contracts are deployed and the network exists:

```bash
# Create network if needed
docker network create prefi_default

# Deploy contracts first
cd ../
npm run deploy:ganache
```

## Tech Stack

- **Framework**: Next.js 14 (App Router)
- **Styling**: Tailwind CSS
- **Web3**: Wagmi + Viem + RainbowKit
- **TypeScript**: Full type safety
- **Docker**: Containerized deployment

## Project Structure

```
nextjs-app/
├── src/
│   ├── app/
│   │   ├── layout.tsx
│   │   ├── page.tsx
│   │   ├── providers.tsx
│   │   └── globals.css
│   ├── components/
│   │   ├── NetworkSelector.tsx
│   │   ├── PredictionForm.tsx
│   │   └── PredictionList.tsx
│   └── lib/
│       └── abis.ts
├── Dockerfile
├── docker-compose.yml
├── next.config.js
├── tailwind.config.ts
└── package.json
```

## Usage

1. **Connect Wallet**: Click "Connect Wallet" button
2. **Select Network**: Choose Ganache/Base/Base Sepolia
3. **Place Prediction**:
   - Enter asset address
   - Set predicted price
   - Lock collateral amount
   - Choose lock duration
4. **Submit**: Confirm transaction in wallet
5. **View**: Track predictions in the list

## Interoperability

The app is fully interoperable with:

- ✅ PredictionMarket.sol
- ✅ PredictionMarket.sol
- ✅ PriceOracle.sol
- ✅ All oracle adapters

## Deployment

### Standalone Deployment

```bash
# Build Docker image
docker build -t prefi-nextjs-app .

# Run container
docker run -p 3000:3000 \
  -e NEXT_PUBLIC_PREDICTION_MARKET_ADDRESS=0x... \
  -e NEXT_PUBLIC_ORACLE_ADDRESS=0x... \
  --network prefi_default \
  prefi-nextjs-app
```

### With Docker Compose

```bash
# Start all services
docker-compose up -d

# Scale if needed
docker-compose up -d --scale nextjs-app=3
```

## Troubleshooting

### "Cannot connect to network"
- Ensure Ganache/Base RPC is accessible
- Check network configuration in providers.tsx

### "Contract not found"
- Verify contract addresses in .env
- Ensure contracts are deployed on selected network

### "Transaction failed"
- Check wallet has sufficient ETH for gas
- Verify contract is approved for collateral token

## License

MIT

---

**Built with ❤️ for decentralized predictions** 🎯