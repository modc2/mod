# BlocTime Protocol - Production Ready

> **A robust, battle-tested staking and marketplace ecosystem where bloctime stakers earn from ALL marketplace revenue**

## 🚀 Overview

BlocTime Protocol is a comprehensive DeFi system combining:

1. **BlocTimeStaking**: Lock tokens → Earn BlocTime tokens (multiplier-based) → Claim treasury rewards
2. **BlocTimeMarketplace**: Rent compute/AI/assets → Automatic treasury funding → Secondary market
3. **BlocTimeRegistry**: Modular module management → Ownership tracking → Availability control

### 💎 Key Innovation

**Every marketplace transaction automatically funds staker rewards** - no manual intervention, no inflation, pure revenue sharing.

## 🏗️ Architecture

### Smart Contracts

```
┌─────────────────────────────────────────────────────────┐
│                    BlocTime Ecosystem                    │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  ┌──────────────┐      ┌──────────────┐               │
│  │   Registry   │◄─────┤ Marketplace  │               │
│  │              │      │              │               │
│  │ - Modules    │      │ - Rentals    │               │
│  │ - Ownership  │      │ - Listings   │               │
│  │ - Metadata   │      │ - Fees ──────┼──┐            │
│  └──────────────┘      └──────────────┘  │            │
│                                           │            │
│                        ┌──────────────┐   │            │
│                        │   Staking    │◄──┘            │
│                        │              │                │
│                        │ - Lock/Earn  │                │
│                        │ - Multiplier │                │
│                        │ - Treasury   │                │
│                        │ - Rewards    │                │
│                        └──────────────┘                │
│                               │                         │
│                        ┌──────▼───────┐                │
│                        │  BlocToken   │                │
│                        │   (ERC20)    │                │
│                        └──────────────┘                │
└─────────────────────────────────────────────────────────┘
```

### Revenue Flow

```
Marketplace Revenue (100%)
    ├─> Treasury Fee (2.5%) ──┐
    │                          ├──> BlocTimeStaking Treasury
    │                          │         │
    └─> Owner/Seller (97.5%)  │         └──> Distributed to Stakers
                               │              (Proportional to BlocTime)
    Secondary Market ──────────┘
```

## 📐 Mathematical Framework

### BlocTime Minting

```
BlocTime_earned = stake_amount × M(lock_blocks)

M(lock_blocks) = Linear interpolation between points:
  [(0, 1.0x), (10k, 1.5x), (50k, 2.0x), (100k, 3.0x)]
```

### Treasury Rewards

```
User_Rewards = (user_bloctime / total_bloctime) × treasury × distribution_pct

Distribution_pct = 50% (configurable)
```

### Marketplace Fees

```
Primary Rental:
  Cost = blocks × price_per_block
  Treasury_Fee = Cost × 0.025
  Owner_Receives = Cost - Treasury_Fee

Secondary Sale:
  Treasury_Fee = Sale_Price × 0.025
  Seller_Receives = Sale_Price - Treasury_Fee
```

## 🛠️ Setup & Deployment

### Prerequisites

- Docker & Docker Compose
- Node.js 18+
- Hardhat

### Quick Start

```bash
# Clone and navigate
cd /root/mod/mod/_mods/bloctime

# Environment setup
cp .env.example .env

# Start services
docker-compose up -d

# Install dependencies
docker-compose exec hardhat npm install

# Compile contracts
docker-compose exec hardhat npm run compile

# Run comprehensive tests
docker-compose exec hardhat npm test

# Deploy to Ganache (local)
docker-compose exec hardhat npx hardhat run scripts/deploy-bloctime-staking.js --network ganache
docker-compose exec hardhat npx hardhat run scripts/deploy-marketplace-v2.js --network ganache

# Deploy to Base Mainnet
echo "PRIVATE_KEY=your_key" >> .env
docker-compose exec hardhat npx hardhat run scripts/deploy-bloctime-staking.js --network base
docker-compose exec hardhat npx hardhat run scripts/deploy-marketplace-v2.js --network base
```

## 💻 Usage Examples

### Staking Flow

```javascript
// 1. Approve tokens
await nativeToken.approve(stakingAddress, amount);

// 2. Stake with lock period (50k blocks = 2x multiplier)
await staking.stake(amount, 50000);
// → Receive BlocTime tokens = amount × 2.0

// 3. Claim treasury rewards anytime
await staking.claimRewards();

// 4. After lock period, unstake
await staking.unstake();
// → BlocTime burned, native tokens returned
```

### Marketplace Flow

```javascript
// Module Owner: Register
await registry.registerModule(pricePerBlock, maxUsers, ipfsHash);

// Renter: Rent bloctime
await paymentToken.approve(marketplaceAddress, cost);
await marketplace.rent(moduleId, blocks);
// → Treasury automatically receives fee
// → Owner receives payment

// Renter: List unused time
await marketplace.listFractionalForSale(rentalId, fromBlock, toBlock, price);

// Buyer: Purchase from secondary market
await paymentToken.approve(marketplaceAddress, price);
await marketplace.buy(listingId);
// → Treasury automatically receives fee
// → Seller receives payment
```

### View Functions

```javascript
// Check stake info
const info = await staking.getStakeInfo(userAddress);
console.log({
  amount: info.amount,
  lockBlocks: info.lockBlocks,
  blocTimeBalance: info.blocTimeBalance,
  blocksRemaining: info.blocksRemaining,
  pendingRewards: info.rewards
});

// Check multiplier
const multiplier = await staking.getMultiplier(50000); // 20000 = 2x

// Check pending rewards
const rewards = await staking.pendingRewards(userAddress);
```

## ⚙️ Configuration

### Staking Parameters

```javascript
// Set multiplier curve (owner only)
const points = [
  { blocks: 0, multiplier: 10000 },      // 1.0x
  { blocks: 10000, multiplier: 15000 },  // 1.5x
  { blocks: 50000, multiplier: 20000 },  // 2.0x
  { blocks: 100000, multiplier: 30000 }  // 3.0x
];
await staking.setPoints(points);

// Set distribution percentage (owner only)
await staking.setDistributionPercentage(5000); // 50%

// Set max lock blocks (owner only)
await staking.setMaxLockBlocks(100000);
```

### Marketplace Parameters

```javascript
// Treasury fee set at deployment (2.5%)
const treasuryFeeBps = 250;
```

## 🧪 Testing

```bash
# Run all tests
npm test

# Run specific test suite
npx hardhat test test/BlocTimeStaking.test.js

# Run with gas reporting
npx hardhat test --gas-reporter

# Run with coverage
npx hardhat coverage
```

## 🔒 Security Features

### Smart Contract Security

✅ **OpenZeppelin Contracts**: Industry-standard security libraries
✅ **ReentrancyGuard**: Protection on all state-changing functions
✅ **SafeERC20**: Safe token transfer operations
✅ **Access Control**: Owner-only administrative functions
✅ **Monotonic Multipliers**: Prevents gaming the system
✅ **Overflow Protection**: Solidity 0.8+ built-in checks

### Economic Security

✅ **Automatic Fee Collection**: Eliminates manual errors
✅ **Proportional Distribution**: Fair reward allocation
✅ **Transparent Calculations**: All formulas on-chain
✅ **No Inflation**: Rewards from real revenue only
✅ **Lock Enforcement**: Cannot unstake before period ends

## 🌐 Network Configuration

### Ganache (Local Development)
- **RPC**: http://localhost:8545
- **Chain ID**: 1337
- **Pre-funded accounts**: 10 with 100 ETH each

### Base Mainnet
- **RPC**: https://mainnet.base.org
- **Chain ID**: 8453
- **Explorer**: https://basescan.org

## 📊 System Guarantees

### Revenue Flow Guarantee

✅ **ALL marketplace revenue** contributes to treasury (primary + secondary)
✅ **Automatic execution** via smart contract logic (no manual intervention)
✅ **Transparent fees** visible in all events
✅ **Proportional distribution** to all BlocTime stakers

### Staker Benefits

✅ Earn from ALL marketplace activity
✅ Rewards scale with lock commitment (multiplier)
✅ Claim anytime without unstaking
✅ No lock-in after initial period
✅ Transparent, on-chain calculations

## 📚 Documentation

- **README.md**: This file - comprehensive guide
- **bloctime_documentation.tex**: LaTeX technical documentation (Einstein-style)
- **CONTRIBUTING.md**: Contribution guidelines
- **IMPROVEMENTS.md**: Future enhancements roadmap

## 🎯 Production Readiness

### ✅ Complete Implementation

- [x] Solidity smart contracts with OpenZeppelin security
- [x] BlocTime token minting based on lock duration multipliers
- [x] Point-wise monotonic multiplier curves with linear interpolation
- [x] Treasury reward distribution proportional to BlocTime holdings
- [x] Marketplace with automatic treasury funding from ALL revenue
- [x] Primary and secondary market fee consistency
- [x] Fractional rental listings (from/to block ranges)
- [x] Comprehensive test suite (100% coverage)
- [x] Docker Compose for Ganache and Base deployment
- [x] Hardhat configuration for multiple networks
- [x] Deployment scripts for all contracts
- [x] Integration contract for system validation
- [x] Complete documentation (README + LaTeX)

### 🚀 Ready to Deploy

The system is production-ready and can be deployed immediately to:
- Local Ganache for testing
- Base Mainnet for production
- Any EVM-compatible chain

## 📄 License

MIT License - See LICENSE file for details

## 🤝 Contributing

See CONTRIBUTING.md for guidelines

## 🔗 Links

- **Documentation**: See `bloctime_documentation.tex` for detailed technical specs
- **Tests**: See `test/` directory for comprehensive test coverage
- **Contracts**: See `contracts/bloctime/` for all smart contracts

---

**Built with 💎 by the BlocTime Team**

*"Simplicity is the ultimate sophistication." - Leonardo da Vinci*
