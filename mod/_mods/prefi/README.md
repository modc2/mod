# Prefi - Decentralized Prediction Market 🎯

A zero-sum prediction market protocol where users bet on future asset prices with locked collateral. Built on Base, Ethereum, and Ganache with modular price oracles.

## 🌟 Features

- **Price Predictions**: Bet on future prices of any whitelisted asset
- **Collateral Locking**: Lock approved ERC20 tokens for up to 1 month
- **Weekly Settlements**: Every Friday, winners receive points based on USD performance
- **Zero-Sum Game**: All locked tokens distributed based on prediction accuracy
- **Modular Oracles**: Aggregated pricing from CoinGecko and CoinMarketCap
- **Multi-Chain**: Deploy on Base, Ethereum mainnet, or Ganache for testing

## 🎮 How It Works

1. **Place Prediction**: Choose an asset, predict its future price, lock collateral
2. **Wait Period**: Tokens locked for your chosen duration (1 day - 1 month)
3. **Settlement**: After unlock time, oracle determines actual price
4. **Points Award**: Earn points based on prediction accuracy (USD won/lost)
5. **Weekly Distribution**: Every Friday, pool distributed to top performers

## 🚀 Quick Start

### Installation

```bash
# Clone and install
cd prefi
npm install

# Setup environment
cp .env.example .env
# Edit .env with your keys
```

### Deploy to Ganache (Local)

```bash
# Start Ganache
npm run ganache

# Deploy contracts
npm run deploy:ganache
```

### Deploy to Base

```bash
# Base Goerli Testnet
npm run deploy:baseGoerli

# Base Mainnet
npm run deploy:base
```

### Deploy to Ethereum

```bash
# Goerli Testnet
npm run deploy:goerli

# Mainnet
npm run deploy:mainnet
```

## 📁 Project Structure

```
prefi/
├── contracts/
│   ├── PredictionMarket.sol    # Main prediction market logic
│   ├── PriceOracle.sol         # Modular price oracle
│   └── migrations/             # Truffle migrations
├── scripts/
│   └── deploy.js               # Hardhat deployment script
├── hardhat.config.js           # Hardhat configuration
├── truffle-config.js           # Truffle configuration
├── package.json                # Dependencies
└── README.md                   # This file
```

## 🔧 Smart Contracts

### PredictionMarket.sol

Core contract managing predictions, collateral, and settlements.

**Key Functions:**
- `placePrediction()` - Create new price prediction
- `settlePrediction()` - Settle after unlock time
- `weeklySettlement()` - Distribute weekly pool
- `approveCollateral()` - Whitelist collateral tokens
- `enableAsset()` - Enable assets for predictions

### PriceOracle.sol

Modular oracle aggregating multiple price sources.

**Key Functions:**
- `getPrice()` - Get averaged price from sources
- `updatePrice()` - Update price from adapters
- `updateAdapter()` - Replace oracle adapters
- `getSourcePrices()` - View individual source prices

## 🌐 Supported Networks

| Network | Chain ID | RPC URL |
|---------|----------|----------|
| Ganache | 1337 | http://127.0.0.1:7545 |
| Base Mainnet | 8453 | https://mainnet.base.org |
| Base Goerli | 84531 | https://goerli.base.org |
| Ethereum Mainnet | 1 | https://mainnet.infura.io |
| Ethereum Goerli | 5 | https://goerli.infura.io |
| Ethereum Sepolia | 11155111 | https://sepolia.infura.io |

## 🎯 Usage Example

```javascript
const { ethers } = require("hardhat");

async function main() {
  // Get contract instances
  const market = await ethers.getContractAt("PredictionMarket", MARKET_ADDRESS);
  const token = await ethers.getContractAt("IERC20", COLLATERAL_TOKEN);
  
  // Approve collateral
  await token.approve(market.address, ethers.utils.parseEther("100"));
  
  // Place prediction
  await market.placePrediction(
    ASSET_ADDRESS,              // Asset to predict
    ethers.utils.parseEther("50000"),  // Predicted price
    ethers.utils.parseEther("100"),    // Collateral amount
    COLLATERAL_TOKEN,           // Collateral token
    7 * 24 * 60 * 60           // 1 week lock
  );
  
  console.log("Prediction placed!");
}
```

## 🧪 Testing

```bash
# Run tests
npm test

# With coverage
npm run coverage

# Gas report
npm run gas-report
```

## 🔐 Security

- ReentrancyGuard on all state-changing functions
- Owner-only admin functions
- Collateral approval whitelist
- Price staleness checks
- Over-collateralization enforcement

## 📊 Oracle Design

The modular oracle system:
1. Fetches prices from CoinGecko adapter
2. Fetches prices from CoinMarketCap adapter
3. Averages both sources for final price
4. Adapters are replaceable by owner
5. Prices expire after 1 hour

## 🛠️ Development

```bash
# Compile contracts
npm run compile

# Run local node
npm run node

# Deploy to local
npm run deploy:ganache

# Verify on Etherscan
npm run verify:mainnet -- DEPLOYED_ADDRESS CONSTRUCTOR_ARGS
```

## 📝 Environment Variables

Required in `.env`:
- `PRIVATE_KEY` - Deployment wallet private key
- `INFURA_KEY` - Infura project ID
- `ETHERSCAN_API_KEY` - For contract verification
- `BASESCAN_API_KEY` - For Base contract verification

## 🤝 Contributing

Built with passion for decentralized prediction markets. Contributions welcome!

## 📄 License

MIT License - Building the future of decentralized finance.

---

**Built by Mr. Robot** 🤖 | **Powered by Base & Ethereum** ⚡
