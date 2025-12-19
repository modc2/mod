# Deployment Guide 🚀

## Prerequisites

1. **Node.js & npm** installed
2. **Wallet** with funds for gas
3. **API Keys** configured in `.env`

## Step-by-Step Deployment

### 1. Setup Environment

```bash
# Install dependencies
npm install

# Copy environment template
cp .env.example .env

# Edit .env with your credentials
nano .env
```

### 2. Compile Contracts

```bash
# Using Hardhat
npx hardhat compile

# Using Truffle
truffle compile
```

### 3. Deploy to Ganache (Local Testing)

```bash
# Terminal 1: Start Ganache
ganache-cli -p 7545 -i 5777

# Terminal 2: Deploy
npx hardhat run scripts/deploy.js --network ganache
```

### 4. Deploy to Base Goerli (Testnet)

```bash
# Ensure you have Base Goerli ETH
# Get from: https://bridge.base.org/

npx hardhat run scripts/deploy.js --network baseGoerli
```

### 5. Deploy to Base Mainnet

```bash
# IMPORTANT: Double-check everything!
# Ensure sufficient ETH for gas

npx hardhat run scripts/deploy.js --network base
```

### 6. Deploy to Ethereum

```bash
# Goerli Testnet
npx hardhat run scripts/deploy.js --network goerli

# Mainnet (CAUTION: Real money!)
npx hardhat run scripts/deploy.js --network mainnet
```

## Post-Deployment

### 1. Verify Contracts

```bash
# Base
npx hardhat verify --network base ORACLE_ADDRESS "ADAPTER1" "ADAPTER2"
npx hardhat verify --network base MARKET_ADDRESS "ORACLE_ADDRESS"

# Ethereum
npx hardhat verify --network mainnet ORACLE_ADDRESS "ADAPTER1" "ADAPTER2"
npx hardhat verify --network mainnet MARKET_ADDRESS "ORACLE_ADDRESS"
```

### 2. Initial Configuration

```javascript
// Approve collateral tokens
await market.approveCollateral(USDC_ADDRESS);
await market.approveCollateral(DAI_ADDRESS);
await market.approveCollateral(WETH_ADDRESS);

// Enable assets for predictions
await market.enableAsset(WBTC_ADDRESS, ethers.utils.parseEther("1000000"));
await market.enableAsset(WETH_ADDRESS, ethers.utils.parseEther("5000000"));
```

### 3. Setup Oracle Adapters

```javascript
// Update oracle adapters if needed
await oracle.updateAdapter("coingecko", COINGECKO_ADAPTER_ADDRESS);
await oracle.updateAdapter("coinmarketcap", CMC_ADAPTER_ADDRESS);
```

## Network-Specific Notes

### Ganache
- **Chain ID**: 1337 or 5777
- **Gas**: Unlimited
- **Speed**: Instant blocks
- **Use**: Local development and testing

### Base Goerli
- **Chain ID**: 84531
- **Faucet**: https://bridge.base.org/
- **Explorer**: https://goerli.basescan.org/
- **Use**: Testnet deployment

### Base Mainnet
- **Chain ID**: 8453
- **Bridge**: https://bridge.base.org/
- **Explorer**: https://basescan.org/
- **Gas**: ~0.001 ETH per deployment

### Ethereum Mainnet
- **Chain ID**: 1
- **Explorer**: https://etherscan.io/
- **Gas**: Variable (check gas prices!)
- **Cost**: ~0.05-0.2 ETH depending on gas

## Troubleshooting

### "Insufficient funds"
- Check wallet balance
- Ensure correct network
- Get testnet ETH from faucets

### "Nonce too low"
- Reset Hardhat: `npx hardhat clean`
- Check pending transactions

### "Contract verification failed"
- Wait 1-2 minutes after deployment
- Ensure constructor args match exactly
- Check API key is valid

### "Gas estimation failed"
- Increase gas limit in config
- Check contract logic for reverts
- Ensure sufficient balance

## Deployment Checklist

- [ ] Dependencies installed
- [ ] `.env` configured with all keys
- [ ] Contracts compiled successfully
- [ ] Wallet has sufficient funds
- [ ] Network RPC is accessible
- [ ] Deployment script tested on testnet
- [ ] Constructor parameters verified
- [ ] Post-deployment config ready
- [ ] Verification commands prepared
- [ ] Backup of deployment addresses

## Gas Estimates

| Contract | Ganache | Base | Ethereum |
|----------|---------|------|----------|
| PriceOracle | Free | ~0.0005 ETH | ~0.02 ETH |
| PredictionMarket | Free | ~0.001 ETH | ~0.05 ETH |
| **Total** | **Free** | **~0.0015 ETH** | **~0.07 ETH** |

*Estimates vary with gas prices*

## Security Reminders

1. **Never commit `.env`** to version control
2. **Use hardware wallet** for mainnet
3. **Test thoroughly** on testnet first
4. **Verify contracts** after deployment
5. **Audit code** before mainnet launch
6. **Start with small amounts** initially
7. **Monitor contracts** after deployment

---

**Ready to deploy? Let's build the future! 🚀**
