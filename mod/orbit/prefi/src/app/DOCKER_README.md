# PreFi - Docker Deployment Guide

## 🚀 Quick Start

This guide will help you run PreFi Prediction Market on Base mainnet using Docker Compose.

### Prerequisites

- Docker Desktop installed ([download here](https://www.docker.com/products/docker-desktop))
- Docker Compose (included with Docker Desktop)
- A WalletConnect Project ID ([get one here](https://cloud.walletconnect.com/))

### Configuration

1. **Copy the environment file:**
   ```bash
   cp .env.example .env
   ```

2. **Edit `.env` and update the following:**
   ```bash
   # Required: WalletConnect Project ID
   NEXT_PUBLIC_WALLETCONNECT_PROJECT_ID=your_project_id_here

   # After contract deployment, update these:
   NEXT_PUBLIC_PREDICTION_MARKET_ADDRESS=0x...
   NEXT_PUBLIC_ORACLE_ADDRESS=0x...
   NEXT_PUBLIC_COLLATERAL_TOKEN=0x...
   ```

### Running with Docker Compose

1. **Build and start the application:**
   ```bash
   docker-compose up -d
   ```

2. **View logs:**
   ```bash
   docker-compose logs -f prefi-app
   ```

3. **Check health status:**
   ```bash
   curl http://localhost:3000/api/health
   ```

4. **Access the application:**
   Open your browser to: http://localhost:3000

5. **Stop the application:**
   ```bash
   docker-compose down
   ```

### Network Configuration

The application is configured for **Base Mainnet** by default:
- Chain ID: 8453
- RPC URL: https://mainnet.base.org
- Uniswap V3 Integration: ✅

### Uniswap V3 Pools on Base

The following Uniswap V3 pools are pre-configured:

| Pair | Pool Address | Fee Tier |
|------|--------------|----------|
| ETH/USDC | 0xd0b53D9277642d899DF5C87A3966A349A798F224 | 0.05% |

### Troubleshooting

**Container won't start:**
```bash
# Check logs
docker-compose logs prefi-app

# Rebuild without cache
docker-compose build --no-cache
docker-compose up -d
```

**Port 3000 already in use:**
```bash
# Edit docker-compose.yml and change the port mapping:
ports:
  - "3001:3000"  # Change 3001 to any available port
```

**Environment variables not loading:**
```bash
# Ensure .env file exists and is in the app directory
ls -la .env

# Restart the container
docker-compose restart prefi-app
```

### Production Deployment

For production deployment:

1. **Use a proper RPC provider:**
   ```bash
   # Update .env with Alchemy or Infura
   NEXT_PUBLIC_ALCHEMY_API_KEY=your_alchemy_key
   NEXT_PUBLIC_BASE_RPC=https://base-mainnet.g.alchemy.com/v2/your_key
   ```

2. **Deploy contracts to Base mainnet:**
   ```bash
   # From the project root
   npx hardhat run scripts/deploy.js --network base
   ```

3. **Update contract addresses in `.env`**

4. **Use Docker secrets for sensitive data:**
   ```bash
   # Instead of .env, use Docker secrets in production
   docker secret create walletconnect_id -
   ```

5. **Enable HTTPS with reverse proxy (nginx/traefik)**

### Docker Commands Reference

```bash
# Start in detached mode
docker-compose up -d

# Start with build
docker-compose up --build

# View logs
docker-compose logs -f

# Stop services
docker-compose down

# Stop and remove volumes
docker-compose down -v

# Restart a specific service
docker-compose restart prefi-app

# Execute command in container
docker-compose exec prefi-app sh

# View running containers
docker-compose ps

# Check resource usage
docker stats prefi-nextjs-app
```

### Health Check

The application includes a health check endpoint:

```bash
curl http://localhost:3000/api/health | jq
```

Response:
```json
{
  "status": "healthy",
  "timestamp": "2024-01-01T00:00:00.000Z",
  "service": "prefi-prediction-market",
  "network": "base-mainnet",
  "version": "1.0.0",
  "chain": {
    "id": "8453",
    "name": "Base",
    "rpc": "https://mainnet.base.org"
  }
}
```

### Development

For development with hot-reload:

```bash
# Run in development mode
npm run dev
# Then access at http://localhost:3000
```

### Architecture

```
┌─────────────────────────────────────┐
│  Docker Container (prefi-app)       │
│  ┌─────────────────────────────┐   │
│  │   Next.js 14 Application    │   │
│  │   - React 18                │   │
│  │   - TailwindCSS             │   │
│  │   - Wagmi + RainbowKit      │   │
│  └─────────────────────────────┘   │
│              ↓                      │
│  ┌─────────────────────────────┐   │
│  │   Port 3000 (Exposed)       │   │
│  └─────────────────────────────┘   │
└─────────────────────────────────────┘
              ↓
┌─────────────────────────────────────┐
│      Base Mainnet (Chain ID 8453)   │
│  ┌─────────────────────────────┐   │
│  │  PreFi Smart Contracts      │   │
│  │  - PredictionMarket         │   │
│  │  - Oracle Adapter           │   │
│  │  - Score Calculator         │   │
│  └─────────────────────────────┘   │
│              ↓                      │
│  ┌─────────────────────────────┐   │
│  │  Uniswap V3 DEX Pools       │   │
│  │  - Price Feeds              │   │
│  └─────────────────────────────┘   │
└─────────────────────────────────────┘
```

### Support

- 📚 Documentation: [README.md](../README.md)
- 🐛 Issues: [GitHub Issues](https://github.com/prefi/issues)
- 💬 Discord: [PreFi Community]
- 🐦 Twitter: [@PreFiProtocol]

### License

MIT License - see LICENSE file for details
