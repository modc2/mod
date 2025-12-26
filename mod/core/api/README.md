# Rent Sharing Smart Contract 🏠

A decentralized rent-sharing mechanism that tracks USD contributions from members using whitelisted stablecoins (USDT, USDC) on Base network.

## Features ✨

- **Multi-Token Support**: Accept USDT and USDC on Base network
- **Member Tracking**: Track individual contributions in USD value
- **Whitelisting**: Owner can add/remove supported tokens
- **Dual Deployment**: Support for both Ganache (local) and Base network
- **Docker Integration**: Easy deployment with docker-compose

## Smart Contract Architecture 🏗️

### RentSharing.sol
- Tracks member contributions in USD
- Supports whitelisted ERC20 tokens (USDT, USDC)
- Owner-controlled member management
- Reentrancy protection
- Event emission for all key actions

## Quick Start 🚀

### Prerequisites
- Docker & Docker Compose
- Node.js 18+
- npm or yarn

### Installation

```bash
# Clone the repository
git clone <repo-url>
cd rent-sharing-contract

# Install dependencies
npm install

# Copy environment file
cp .env.example .env
# Edit .env with your configuration
```

### Deployment

#### Deploy to Ganache (Local)

```bash
# Start Ganache and deploy
npm run deploy:ganache

# Or run Ganache separately
npm run ganache
# Then in another terminal
npx hardhat run scripts/deploy.js --network ganache
```

#### Deploy to Base Network

```bash
# Make sure .env has BASE_RPC_URL and PRIVATE_KEY set
npm run deploy:base

# Or manually
npx hardhat run scripts/deploy.js --network base
```

## Docker Compose Services 🐳

### Ganache Service
- Runs local Ethereum blockchain
- Port: 8545
- Pre-funded accounts with 1000 ETH each

### Hardhat-Ganache Service
- Deploys contracts to Ganache
- Creates mock USDT/USDC tokens

### Hardhat-Base Service
- Deploys contracts to Base network
- Uses real USDT/USDC addresses

## Contract Interaction 💻

### Add Member
```javascript
await rentSharing.addMember(memberAddress);
```

### Contribute Rent
```javascript
// Approve token first
await usdt.approve(rentSharingAddress, amount);
// Make contribution
await rentSharing.contribute(usdtAddress, amount, usdValue);
```

### Check Contribution
```javascript
const contribution = await rentSharing.getMemberContribution(memberAddress);
console.log('Total USD contributed:', contribution.toString());
```

## Network Configuration 🌐

### Base Mainnet
- Chain ID: 8453
- RPC: https://mainnet.base.org
- USDT: 0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2
- USDC: 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913

### Ganache (Local)
- Chain ID: 1337
- RPC: http://localhost:8545
- Mock tokens deployed automatically

## Project Structure 📁

```
.
├── contracts/
│   ├── RentSharing.sol      # Main contract
│   └── MockERC20.sol        # Mock tokens for testing
├── scripts/
│   └── deploy.js            # Deployment script
├── deployments/             # Deployment artifacts
├── docker-compose.yml       # Docker services
├── Dockerfile.ganache       # Ganache deployment
├── Dockerfile.base          # Base deployment
├── hardhat.config.js        # Hardhat configuration
└── package.json
```

## Security Features 🔒

- ReentrancyGuard protection
- Ownable access control
- Token whitelist validation
- Member validation
- SafeERC20 transfers

## Events 📡

- `MemberAdded(address indexed member)`
- `ContributionMade(address indexed member, address indexed token, uint256 amount, uint256 usdValue)`
- `TokenWhitelisted(address indexed token)`
- `TokenRemoved(address indexed token)`

## Development 🛠️

```bash
# Compile contracts
npm run compile

# Run tests
npm run test

# Clean artifacts
npx hardhat clean
```

## License 📄

MIT License

---

*Built with precision for decentralized rent sharing* 🚀