# Newma - Base Network Smart Contract

A simple ERC20 token deployed on Base Network with a Next.js frontend.

## Project Structure

```
newma/
├── contracts/          # Solidity smart contracts
│   ├── Newma.sol      # Main ERC20 token contract
│   ├── hardhat.config.js
│   ├── package.json
│   ├── Dockerfile
│   └── scripts/
│       └── deploy.js  # Deployment script
├── frontend/          # Next.js frontend
│   ├── app/
│   │   ├── page.tsx
│   │   ├── layout.tsx
│   │   └── globals.css
│   ├── package.json
│   ├── next.config.js
│   └── Dockerfile
├── docker-compose.yml
├── .env.example
└── README.md
```

## Quick Start

### 1. Setup Environment

```bash
cp .env.example .env
# Edit .env with your private key and RPC URLs
```

### 2. Deploy Contracts

```bash
cd contracts
npm install
npm run compile
npm run deploy
```

### 3. Run Frontend

```bash
cd frontend
npm install
npm run dev
```

### 4. Docker (Full Stack)

```bash
docker-compose up --build
```

## Contract Details

- **Name**: Newma
- **Symbol**: NEWMA
- **Max Supply**: 1,000,000,000 (1 billion)
- **Network**: Base (Chain ID: 8453)
- **Test Network**: Base Sepolia (Chain ID: 84532)

## Features

- ERC20 compliant token
- Burn functionality
- Owner controlled
- Base Network optimized

## License

MIT
