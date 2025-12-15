# BlocTime Protocol - Deployment Guide

## Modular Architecture

The BlocTime protocol consists of independently deployable contracts that work together:

### Core Contracts

1. **PaymentTokenWhitelist.sol** - Manages whitelisted payment tokens
2. **Registry.sol** - Module registration and metadata
3. **Staking.sol** - Staking system with BlocTimeToken
4. **BidSystem.sol** - Bidding system for rental slots
5. **Marketplace.sol** - Multi-token marketplace with fractional rentals
6. **Integration.sol** - System health checks and validation

## Deployment Order

### 1. Deploy PaymentTokenWhitelist
```javascript
const PaymentTokenWhitelist = await ethers.getContractFactory('PaymentTokenWhitelist');
const whitelist = await PaymentTokenWhitelist.deploy();
await whitelist.waitForDeployment();
```

### 2. Deploy Native Token (or use existing)
```javascript
const MockERC20 = await ethers.getContractFactory('MockERC20');
const nativeToken = await MockERC20.deploy('Native Token', 'NAT', ethers.parseEther('1000000'));
await nativeToken.waitForDeployment();
```

### 3. Deploy Staking System
```javascript
const BlocTimeStaking = await ethers.getContractFactory('BlocTimeStaking');
const staking = await BlocTimeStaking.deploy(
    await nativeToken.getAddress(),
    'BlocTime Token',
    'BLOC',
    100000, // maxLockBlocks
    5000    // 50% distribution
);
await staking.waitForDeployment();

// Set multiplier points
const points = [
    { blocks: 0, multiplier: 10000 },
    { blocks: 10000, multiplier: 15000 },
    { blocks: 50000, multiplier: 20000 },
    { blocks: 100000, multiplier: 30000 }
];
await staking.setPoints(points);
```

### 4. Deploy Registry
```javascript
const BlocTimeRegistry = await ethers.getContractFactory('BlocTimeRegistry');
const registry = await BlocTimeRegistry.deploy();
await registry.waitForDeployment();
```

### 5. Deploy Marketplace
```javascript
const BlocTimeMarketplaceMultiToken = await ethers.getContractFactory('BlocTimeMarketplaceMultiToken');
const marketplace = await BlocTimeMarketplaceMultiToken.deploy(
    await whitelist.getAddress(),
    await nativeToken.getAddress(),
    await staking.getAddress(),
    await registry.getAddress(),
    250 // 2.5% treasury fee
);
await marketplace.waitForDeployment();
```

### 6. Whitelist Payment Tokens
```javascript
// Whitelist native token
await whitelist.whitelistToken(await nativeToken.getAddress());

// Whitelist other tokens as needed
await whitelist.whitelistToken(USDC_ADDRESS);
await whitelist.whitelistToken(DAI_ADDRESS);
```

### 7. Deploy Integration (Optional - for monitoring)
```javascript
const BlocTimeIntegration = await ethers.getContractFactory('BlocTimeIntegration');
const integration = await BlocTimeIntegration.deploy(
    await marketplace.getAddress(),
    await registry.getAddress(),
    await staking.getAddress()
);
await integration.waitForDeployment();
```

## Verification

### Health Check
```javascript
const [marketplaceHealthy, registryHealthy, stakingHealthy, status] = await integration.healthCheck();
console.log('Marketplace:', marketplaceHealthy ? '✅' : '❌');
console.log('Registry:', registryHealthy ? '✅' : '❌');
console.log('Staking:', stakingHealthy ? '✅' : '❌');
console.log('Status:', status);
```

### Validate Module Registration
```javascript
const [valid, reason] = await integration.validateModuleRegistration(moduleId);
console.log('Module Valid:', valid, reason);
```

### Validate Rental Flow
```javascript
const [valid, reason] = await integration.validateRentalFlow(rentalId);
console.log('Rental Valid:', valid, reason);
```

## Contract Addresses Template

```bash
# Mainnet Deployment
PAYMENT_WHITELIST_ADDRESS=0x...
NATIVE_TOKEN_ADDRESS=0x...
STAKING_ADDRESS=0x...
BLOCTIME_TOKEN_ADDRESS=0x...
REGISTRY_ADDRESS=0x...
MARKETPLACE_ADDRESS=0x...
BID_SYSTEM_ADDRESS=0x...
INTEGRATION_ADDRESS=0x...
```

## Deployment Scripts

### Quick Deploy (All Contracts)
```bash
npx hardhat run scripts/deploy.js --network <network>
```

### Individual Contract Deployment
```bash
# Deploy only whitelist
npx hardhat run scripts/deploy-whitelist.js --network <network>

# Deploy only staking
npx hardhat run scripts/deploy-staking.js --network <network>

# Deploy only marketplace
npx hardhat run scripts/deploy-marketplace.js --network <network>
```

## Post-Deployment Configuration

### 1. Configure Whitelist
```javascript
// Add payment tokens
await whitelist.whitelistToken(TOKEN_ADDRESS);

// Remove tokens if needed
await whitelist.delistToken(TOKEN_ADDRESS);
```

### 2. Configure Staking
```javascript
// Update multiplier points
await staking.setPoints(newPoints);

// Update max lock blocks
await staking.setMaxLockBlocks(200000);

// Update distribution percentage
await staking.setDistributionPercentage(7500); // 75%
```

### 3. Transfer Ownership (if needed)
```javascript
await whitelist.transferOwnership(NEW_OWNER);
await staking.transferOwnership(NEW_OWNER);
```

## Testing Deployment

### Local Network
```bash
# Start local node
npx hardhat node

# Deploy to local network
npx hardhat run scripts/deploy.js --network localhost

# Setup test environment
npx hardhat run scripts/setup-local.js --network localhost
```

### Testnet
```bash
# Deploy to testnet
npx hardhat run scripts/deploy.js --network sepolia

# Verify contracts
npx hardhat run scripts/verify.js --network sepolia
```

## Security Considerations

1. **Access Control**: Ensure proper ownership transfer after deployment
2. **Token Whitelist**: Only whitelist trusted ERC20 tokens
3. **Fee Configuration**: Set reasonable treasury fees (recommended: 1-5%)
4. **Multiplier Points**: Validate monotonicity before setting
5. **Emergency Functions**: Keep emergency withdraw functions secure

## Upgrade Path

Contracts are designed to be modular and independently upgradeable:

1. Deploy new version of contract
2. Update references in dependent contracts
3. Migrate state if necessary
4. Deprecate old contract

## Monitoring

Use the Integration contract for ongoing system monitoring:

```javascript
// Get system statistics
const [totalModules, totalRentals, totalStaked, totalBlocTime, treasuryBalance] = 
    await integration.getSystemStats();

// Regular health checks
setInterval(async () => {
    const [mHealthy, rHealthy, sHealthy, status] = await integration.healthCheck();
    if (!mHealthy || !rHealthy || !sHealthy) {
        alert('System degraded: ' + status);
    }
}, 60000); // Every minute
```

## Support

For deployment issues or questions:
- GitHub Issues: [repository]/issues
- Documentation: /docs
- Community: Discord/Telegram
