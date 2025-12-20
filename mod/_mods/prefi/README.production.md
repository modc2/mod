# Production Deployment Guide - Ganache & Base

## 🎯 Quick Start

### Prerequisites
```bash
# Install dependencies
npm install

# Setup environment
cp .env.example .env
# Edit .env with your credentials
```

### Ganache Deployment (Local Testing)

```bash
# Terminal 1: Start Ganache
ganache-cli -p 7545 -i 1337 -m "test test test test test test test test test test test junk"

# Terminal 2: Deploy
npm run deploy:ganache
```

### Base Sepolia Deployment (Testnet)

```bash
# Get testnet ETH from Base Sepolia faucet
# https://www.coinbase.com/faucets/base-ethereum-sepolia-faucet

# Deploy to Base Sepolia
npm run deploy:baseSepolia
```

### Base Mainnet Deployment (Production)

```bash
# ⚠️ PRODUCTION - Use with caution!
# Ensure you have sufficient ETH (~0.005 ETH recommended)

npm run deploy:base
```

## 🔧 Configuration

### Environment Variables (.env)

```env
# Ganache
GANACHE_URL=http://127.0.0.1:7545

# Base Networks
BASE_RPC_URL=https://mainnet.base.org
BASE_SEPOLIA_RPC_URL=https://sepolia.base.org

# Wallet (choose one method)
PRIVATE_KEY=0x...
# OR
MNEMONIC=word1 word2 ... word12

# API Keys
BASESCAN_API_KEY=ABC123...
COINMARKETCAP_API_KEY=xyz789...
```

## 📊 Network Details

### Ganache (Local)
- **Chain ID**: 1337
- **RPC**: http://127.0.0.1:7545
- **Gas**: Unlimited
- **Cost**: Free
- **Use**: Development & Testing

### Base Sepolia (Testnet)
- **Chain ID**: 84532
- **RPC**: https://sepolia.base.org
- **Faucet**: https://www.coinbase.com/faucets/base-ethereum-sepolia-faucet
- **Explorer**: https://sepolia.basescan.org
- **Cost**: Free (testnet ETH)
- **Use**: Pre-production Testing

### Base Mainnet (Production)
- **Chain ID**: 8453
- **RPC**: https://mainnet.base.org
- **Bridge**: https://bridge.base.org
- **Explorer**: https://basescan.org
- **Cost**: ~0.002-0.005 ETH
- **Use**: Production Deployment

## 🚀 Deployment Process

### 1. Compile Contracts
```bash
npm run compile
```

### 2. Run Tests (Optional but Recommended)
```bash
npm test
```

### 3. Deploy to Target Network
```bash
# Ganache
npm run deploy:ganache

# Base Sepolia
npm run deploy:baseSepolia

# Base Mainnet
npm run deploy:base
```

### 4. Verify Contracts (Base only)
```bash
# Automatic verification included in deploy script
# Or manually:
npx hardhat verify --network base <CONTRACT_ADDRESS> <CONSTRUCTOR_ARGS>
```

## 🔐 Security Checklist

- [ ] `.env` file is in `.gitignore`
- [ ] Private keys are secure (use hardware wallet for mainnet)
- [ ] Tested thoroughly on Ganache
- [ ] Tested on Base Sepolia testnet
- [ ] Contract code audited (for mainnet)
- [ ] Gas estimates reviewed
- [ ] Sufficient ETH in deployer wallet
- [ ] Backup of deployment addresses
- [ ] Emergency pause mechanism tested

## 📈 Gas Estimates

| Contract | Ganache | Base Sepolia | Base Mainnet |
|----------|---------|--------------|---------------|
| PriceOracle | Free | Free | ~0.0008 ETH |
| Adapters (2x) | Free | Free | ~0.0012 ETH |
| PredictionMarket | Free | Free | ~0.0015 ETH |
| **Total** | **Free** | **Free** | **~0.0035 ETH** |

*Estimates based on current gas prices (~0.5 gwei on Base)*

## 🛠️ Post-Deployment

### Initialize Contracts

```javascript
// 1. Approve collateral tokens
await market.approveCollateral(USDC_ADDRESS);
await market.approveCollateral(WETH_ADDRESS);

// 2. Enable prediction assets
await market.enableAsset(WBTC_ADDRESS, ethers.utils.parseEther("1000000"));
await market.enableAsset(WETH_ADDRESS, ethers.utils.parseEther("5000000"));

// 3. Add assets to oracle adapters
await cgAdapter.addAsset(WBTC_ADDRESS, "bitcoin");
await cmcAdapter.addAsset(WBTC_ADDRESS, "1");
```

### Monitor Deployment

```bash
# Check deployment files
ls -la deployments/

# View latest deployment
cat deployments/base-latest.json
```

## 🐛 Troubleshooting

### "Insufficient funds"
- Check wallet balance: `npx hardhat run scripts/check-balance.js --network <network>`
- Get testnet ETH from faucet (Sepolia)
- Bridge ETH to Base (Mainnet)

### "Nonce too low"
```bash
# Reset Hardhat cache
npx hardhat clean
rm -rf cache artifacts
```

### "Contract verification failed"
- Wait 1-2 minutes after deployment
- Ensure BASESCAN_API_KEY is set
- Verify constructor arguments match exactly

### Ganache Connection Issues
```bash
# Ensure Ganache is running on correct port
ganache-cli -p 7545 -i 1337

# Check connection
curl -X POST http://127.0.0.1:7545 \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}'
```

## 📞 Support

- **Base Docs**: https://docs.base.org
- **Hardhat Docs**: https://hardhat.org/docs
- **Ganache Docs**: https://trufflesuite.com/ganache

## 🎉 Success!

Your contracts are now deployed and ready for:
- ✅ Local testing (Ganache)
- ✅ Testnet validation (Base Sepolia)
- ✅ Production use (Base Mainnet)

**Next**: Configure oracles, add liquidity, and start accepting predictions!
