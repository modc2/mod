# BlocTime Protocol - Integration Guide

## Quick Start Integration

### For DApp Developers

Integrate BlocTime staking and marketplace into your application in 3 steps:

#### 1. Install Dependencies

```bash
npm install ethers@^6.0.0
```

#### 2. Initialize Contracts

```javascript
import { ethers } from 'ethers';

// Contract ABIs (import from artifacts)
import StakingABI from './artifacts/BlocTimeStaking.json';
import MarketplaceABI from './artifacts/BlocTimeMarketplaceMultiToken.json';
import RegistryABI from './artifacts/BlocTimeRegistry.json';

// Contract addresses (from deployment)
const ADDRESSES = {
  staking: '0x...',
  marketplace: '0x...',
  registry: '0x...',
  nativeToken: '0x...'
};

// Initialize provider and signer
const provider = new ethers.BrowserProvider(window.ethereum);
const signer = await provider.getSigner();

// Initialize contracts
const staking = new ethers.Contract(ADDRESSES.staking, StakingABI.abi, signer);
const marketplace = new ethers.Contract(ADDRESSES.marketplace, MarketplaceABI.abi, signer);
const registry = new ethers.Contract(ADDRESSES.registry, RegistryABI.abi, signer);
```

#### 3. Implement Core Features

```javascript
// Staking
async function stakeTokens(amount, lockBlocks) {
  const tx1 = await nativeToken.approve(ADDRESSES.staking, amount);
  await tx1.wait();
  
  const tx2 = await staking.stake(amount, lockBlocks);
  await tx2.wait();
  
  return tx2.hash;
}

// Renting
async function rentModule(moduleId, blocks, paymentToken) {
  const module = await registry.getModule(moduleId);
  const cost = module.pricePerBlock * BigInt(blocks);
  
  const tx1 = await paymentToken.approve(ADDRESSES.marketplace, cost);
  await tx1.wait();
  
  const tx2 = await marketplace.rent(moduleId, blocks, paymentToken);
  const receipt = await tx2.wait();
  
  // Extract rental ID from event
  const event = receipt.logs.find(log => log.eventName === 'Rented');
  return event.args.rentalId;
}
```

---

## Frontend Integration

### React Hook Example

```javascript
import { useState, useEffect } from 'react';
import { ethers } from 'ethers';

export function useBlocTimeStaking(stakingAddress) {
  const [stakeInfo, setStakeInfo] = useState(null);
  const [loading, setLoading] = useState(false);
  
  useEffect(() => {
    loadStakeInfo();
  }, []);
  
  async function loadStakeInfo() {
    const provider = new ethers.BrowserProvider(window.ethereum);
    const signer = await provider.getSigner();
    const address = await signer.getAddress();
    
    const staking = new ethers.Contract(stakingAddress, StakingABI.abi, provider);
    const info = await staking.getStakeInfo(address);
    
    setStakeInfo({
      amount: ethers.formatEther(info.amount),
      blocTimeBalance: ethers.formatEther(info.blocTimeBalance),
      blocksRemaining: info.blocksRemaining.toString(),
      pendingRewards: ethers.formatEther(info.rewards)
    });
  }
  
  async function stake(amount, lockBlocks) {
    setLoading(true);
    try {
      const provider = new ethers.BrowserProvider(window.ethereum);
      const signer = await provider.getSigner();
      const staking = new ethers.Contract(stakingAddress, StakingABI.abi, signer);
      
      const tx = await staking.stake(
        ethers.parseEther(amount),
        lockBlocks
      );
      await tx.wait();
      
      await loadStakeInfo();
      return { success: true, hash: tx.hash };
    } catch (error) {
      return { success: false, error: error.message };
    } finally {
      setLoading(false);
    }
  }
  
  async function claimRewards() {
    setLoading(true);
    try {
      const provider = new ethers.BrowserProvider(window.ethereum);
      const signer = await provider.getSigner();
      const staking = new ethers.Contract(stakingAddress, StakingABI.abi, signer);
      
      const tx = await staking.claimRewards();
      await tx.wait();
      
      await loadStakeInfo();
      return { success: true, hash: tx.hash };
    } catch (error) {
      return { success: false, error: error.message };
    } finally {
      setLoading(false);
    }
  }
  
  return { stakeInfo, loading, stake, claimRewards, refresh: loadStakeInfo };
}
```

### Vue.js Composable Example

```javascript
import { ref, onMounted } from 'vue';
import { ethers } from 'ethers';

export function useBlocTimeMarketplace(marketplaceAddress, registryAddress) {
  const modules = ref([]);
  const loading = ref(false);
  
  onMounted(() => {
    loadModules();
  });
  
  async function loadModules() {
    loading.value = true;
    try {
      const provider = new ethers.BrowserProvider(window.ethereum);
      const registry = new ethers.Contract(registryAddress, RegistryABI.abi, provider);
      
      const nextId = await registry.nextModuleId();
      const moduleList = [];
      
      for (let i = 1; i < nextId; i++) {
        const module = await registry.getModule(i);
        if (module.active) {
          moduleList.push({
            id: i,
            owner: module.owner,
            pricePerBlock: ethers.formatEther(module.pricePerBlock),
            maxUsers: module.maxConcurrentUsers.toString(),
            currentUsers: module.currentUsers.toString(),
            ipfsHash: module.ipfsHash
          });
        }
      }
      
      modules.value = moduleList;
    } finally {
      loading.value = false;
    }
  }
  
  async function rentModule(moduleId, blocks, paymentToken) {
    loading.value = true;
    try {
      const provider = new ethers.BrowserProvider(window.ethereum);
      const signer = await provider.getSigner();
      const marketplace = new ethers.Contract(marketplaceAddress, MarketplaceABI.abi, signer);
      const registry = new ethers.Contract(registryAddress, RegistryABI.abi, provider);
      
      const module = await registry.getModule(moduleId);
      const cost = module.pricePerBlock * BigInt(blocks);
      
      // Approve
      const token = new ethers.Contract(paymentToken, ERC20ABI, signer);
      const tx1 = await token.approve(marketplaceAddress, cost);
      await tx1.wait();
      
      // Rent
      const tx2 = await marketplace.rent(moduleId, blocks, paymentToken);
      const receipt = await tx2.wait();
      
      return { success: true, hash: tx2.hash };
    } catch (error) {
      return { success: false, error: error.message };
    } finally {
      loading.value = false;
    }
  }
  
  return { modules, loading, loadModules, rentModule };
}
```

---

## Backend Integration

### Node.js Service Example

```javascript
const { ethers } = require('ethers');

class BlocTimeService {
  constructor(config) {
    this.provider = new ethers.JsonRpcProvider(config.rpcUrl);
    this.wallet = new ethers.Wallet(config.privateKey, this.provider);
    
    this.staking = new ethers.Contract(
      config.addresses.staking,
      StakingABI.abi,
      this.wallet
    );
    
    this.marketplace = new ethers.Contract(
      config.addresses.marketplace,
      MarketplaceABI.abi,
      this.wallet
    );
    
    this.registry = new ethers.Contract(
      config.addresses.registry,
      RegistryABI.abi,
      this.wallet
    );
  }
  
  async getSystemStats() {
    const integration = new ethers.Contract(
      this.config.addresses.integration,
      IntegrationABI.abi,
      this.provider
    );
    
    const stats = await integration.getSystemStats();
    
    return {
      totalModules: stats.totalModules.toString(),
      totalRentals: stats.totalRentals.toString(),
      totalStaked: ethers.formatEther(stats.totalStaked),
      totalBlocTime: ethers.formatEther(stats.totalBlocTime),
      treasuryBalance: ethers.formatEther(stats.treasuryBalance)
    };
  }
  
  async monitorRentals() {
    // Listen for rental events
    this.marketplace.on('Rented', (rentalId, moduleId, renter, blocks, cost, paymentToken, event) => {
      console.log('New rental:', {
        rentalId: rentalId.toString(),
        moduleId: moduleId.toString(),
        renter,
        blocks: blocks.toString(),
        cost: ethers.formatEther(cost),
        paymentToken,
        txHash: event.log.transactionHash
      });
      
      // Store in database, send notifications, etc.
    });
  }
  
  async autoRegisterModule(pricePerBlock, maxUsers, metadata) {
    // Upload metadata to IPFS
    const ipfsHash = await this.uploadToIPFS(metadata);
    
    // Register module
    const tx = await this.registry.registerModule(
      ethers.parseEther(pricePerBlock),
      maxUsers,
      ipfsHash
    );
    
    const receipt = await tx.wait();
    const event = receipt.logs.find(log => log.eventName === 'ModuleRegistered');
    
    return {
      moduleId: event.args.moduleId.toString(),
      txHash: tx.hash
    };
  }
  
  async uploadToIPFS(metadata) {
    // Implement IPFS upload
    // Return IPFS hash
  }
}

// Usage
const service = new BlocTimeService({
  rpcUrl: 'https://mainnet.base.org',
  privateKey: process.env.PRIVATE_KEY,
  addresses: {
    staking: '0x...',
    marketplace: '0x...',
    registry: '0x...',
    integration: '0x...'
  }
});

await service.monitorRentals();
const stats = await service.getSystemStats();
console.log(stats);
```

---

## Event Monitoring

### Real-time Event Listener

```javascript
class BlocTimeEventMonitor {
  constructor(provider, addresses) {
    this.staking = new ethers.Contract(addresses.staking, StakingABI.abi, provider);
    this.marketplace = new ethers.Contract(addresses.marketplace, MarketplaceABI.abi, provider);
    this.registry = new ethers.Contract(addresses.registry, RegistryABI.abi, provider);
  }
  
  startMonitoring(callbacks) {
    // Staking events
    this.staking.on('Staked', (user, amount, lockBlocks, blocTimeEarned, event) => {
      if (callbacks.onStaked) {
        callbacks.onStaked({
          user,
          amount: ethers.formatEther(amount),
          lockBlocks: lockBlocks.toString(),
          blocTimeEarned: ethers.formatEther(blocTimeEarned),
          blockNumber: event.log.blockNumber,
          txHash: event.log.transactionHash
        });
      }
    });
    
    this.staking.on('RewardsClaimed', (user, amount, event) => {
      if (callbacks.onRewardsClaimed) {
        callbacks.onRewardsClaimed({
          user,
          amount: ethers.formatEther(amount),
          blockNumber: event.log.blockNumber,
          txHash: event.log.transactionHash
        });
      }
    });
    
    // Marketplace events
    this.marketplace.on('Rented', (rentalId, moduleId, renter, blocks, cost, paymentToken, event) => {
      if (callbacks.onRented) {
        callbacks.onRented({
          rentalId: rentalId.toString(),
          moduleId: moduleId.toString(),
          renter,
          blocks: blocks.toString(),
          cost: ethers.formatEther(cost),
          paymentToken,
          blockNumber: event.log.blockNumber,
          txHash: event.log.transactionHash
        });
      }
    });
    
    this.marketplace.on('Sold', (listingId, buyer, price, paymentToken, event) => {
      if (callbacks.onSold) {
        callbacks.onSold({
          listingId: listingId.toString(),
          buyer,
          price: ethers.formatEther(price),
          paymentToken,
          blockNumber: event.log.blockNumber,
          txHash: event.log.transactionHash
        });
      }
    });
    
    // Registry events
    this.registry.on('ModuleRegistered', (moduleId, owner, pricePerBlock, event) => {
      if (callbacks.onModuleRegistered) {
        callbacks.onModuleRegistered({
          moduleId: moduleId.toString(),
          owner,
          pricePerBlock: ethers.formatEther(pricePerBlock),
          blockNumber: event.log.blockNumber,
          txHash: event.log.transactionHash
        });
      }
    });
  }
  
  stopMonitoring() {
    this.staking.removeAllListeners();
    this.marketplace.removeAllListeners();
    this.registry.removeAllListeners();
  }
}

// Usage
const monitor = new BlocTimeEventMonitor(provider, addresses);

monitor.startMonitoring({
  onStaked: (data) => console.log('Stake event:', data),
  onRewardsClaimed: (data) => console.log('Rewards claimed:', data),
  onRented: (data) => console.log('Rental created:', data),
  onSold: (data) => console.log('Listing sold:', data),
  onModuleRegistered: (data) => console.log('Module registered:', data)
});
```

---

## Testing Integration

### Hardhat Test Example

```javascript
const { expect } = require('chai');
const { ethers } = require('hardhat');

describe('BlocTime Integration', function() {
  let staking, marketplace, registry, nativeToken;
  let owner, alice, bob;
  
  beforeEach(async function() {
    [owner, alice, bob] = await ethers.getSigners();
    
    // Deploy contracts
    const MockERC20 = await ethers.getContractFactory('MockERC20');
    nativeToken = await MockERC20.deploy('Native', 'NAT', ethers.parseEther('1000000'));
    
    const BlocTimeStaking = await ethers.getContractFactory('BlocTimeStaking');
    staking = await BlocTimeStaking.deploy(
      await nativeToken.getAddress(),
      'BlocTime',
      'BLOC',
      100000,
      5000
    );
    
    // ... deploy other contracts
  });
  
  it('should complete full staking flow', async function() {
    const amount = ethers.parseEther('1000');
    const lockBlocks = 50000;
    
    // Transfer tokens to Alice
    await nativeToken.transfer(alice.address, amount);
    
    // Alice stakes
    await nativeToken.connect(alice).approve(await staking.getAddress(), amount);
    await staking.connect(alice).stake(amount, lockBlocks);
    
    // Check stake info
    const info = await staking.getStakeInfo(alice.address);
    expect(info.amount).to.equal(amount);
    expect(info.lockBlocks).to.equal(lockBlocks);
    
    // Fund treasury
    const treasuryAmount = ethers.parseEther('100');
    await nativeToken.approve(await staking.getAddress(), treasuryAmount);
    await staking.fundTreasury(treasuryAmount);
    
    // Check pending rewards
    const rewards = await staking.pendingRewards(alice.address);
    expect(rewards).to.be.gt(0);
    
    // Claim rewards
    await staking.connect(alice).claimRewards();
  });
});
```

---

## Security Best Practices

### 1. Always Validate Inputs

```javascript
function validateStakeParams(amount, lockBlocks) {
  if (amount <= 0) throw new Error('Amount must be positive');
  if (lockBlocks > MAX_LOCK_BLOCKS) throw new Error('Lock period too long');
  if (lockBlocks < 0) throw new Error('Lock period must be non-negative');
}
```

### 2. Handle Errors Gracefully

```javascript
async function safeStake(amount, lockBlocks) {
  try {
    validateStakeParams(amount, lockBlocks);
    
    const tx = await staking.stake(amount, lockBlocks);
    await tx.wait();
    
    return { success: true, hash: tx.hash };
  } catch (error) {
    console.error('Stake failed:', error);
    
    // Parse error message
    if (error.message.includes('Already staking')) {
      return { success: false, error: 'You already have an active stake' };
    }
    
    return { success: false, error: error.message };
  }
}
```

### 3. Use Gas Estimation

```javascript
async function estimateStakeGas(amount, lockBlocks) {
  try {
    const gasEstimate = await staking.stake.estimateGas(amount, lockBlocks);
    const gasPrice = await provider.getFeeData();
    
    return {
      gasLimit: gasEstimate,
      gasPrice: gasPrice.gasPrice,
      estimatedCost: gasEstimate * gasPrice.gasPrice
    };
  } catch (error) {
    console.error('Gas estimation failed:', error);
    return null;
  }
}
```

### 4. Implement Retry Logic

```javascript
async function retryTransaction(txFunction, maxRetries = 3) {
  for (let i = 0; i < maxRetries; i++) {
    try {
      const tx = await txFunction();
      const receipt = await tx.wait();
      return { success: true, receipt };
    } catch (error) {
      if (i === maxRetries - 1) {
        return { success: false, error };
      }
      await new Promise(resolve => setTimeout(resolve, 1000 * (i + 1)));
    }
  }
}
```

---

## Deployment Integration

### Automated Deployment Script

```javascript
const hre = require('hardhat');

async function deployBlocTime(config) {
  console.log('Deploying BlocTime Protocol...');
  
  const [deployer] = await hre.ethers.getSigners();
  console.log('Deployer:', deployer.address);
  
  // Deploy in correct order
  const addresses = {};
  
  // 1. Native token (or use existing)
  if (config.nativeTokenAddress) {
    addresses.nativeToken = config.nativeTokenAddress;
  } else {
    const MockERC20 = await hre.ethers.getContractFactory('MockERC20');
    const nativeToken = await MockERC20.deploy(
      config.tokenName,
      config.tokenSymbol,
      config.tokenSupply
    );
    await nativeToken.waitForDeployment();
    addresses.nativeToken = await nativeToken.getAddress();
  }
  
  // 2. Staking
  const BlocTimeStaking = await hre.ethers.getContractFactory('BlocTimeStaking');
  const staking = await BlocTimeStaking.deploy(
    addresses.nativeToken,
    config.blocTimeName,
    config.blocTimeSymbol,
    config.maxLockBlocks,
    config.distributionPercentage
  );
  await staking.waitForDeployment();
  addresses.staking = await staking.getAddress();
  addresses.blocTimeToken = await staking.blocTimeToken();
  
  // Set multiplier points
  await staking.setPoints(config.multiplierPoints);
  
  // 3. Registry
  const BlocTimeRegistry = await hre.ethers.getContractFactory('BlocTimeRegistry');
  const registry = await BlocTimeRegistry.deploy();
  await registry.waitForDeployment();
  addresses.registry = await registry.getAddress();
  
  // 4. Whitelist
  const PaymentTokenWhitelist = await hre.ethers.getContractFactory('PaymentTokenWhitelist');
  const whitelist = await PaymentTokenWhitelist.deploy();
  await whitelist.waitForDeployment();
  addresses.whitelist = await whitelist.getAddress();
  
  // Whitelist native token
  await whitelist.whitelistToken(addresses.nativeToken);
  
  // 5. Marketplace
  const BlocTimeMarketplaceMultiToken = await hre.ethers.getContractFactory('BlocTimeMarketplaceMultiToken');
  const marketplace = await BlocTimeMarketplaceMultiToken.deploy(
    addresses.whitelist,
    addresses.nativeToken,
    addresses.staking,
    addresses.registry,
    config.treasuryFeeBps
  );
  await marketplace.waitForDeployment();
  addresses.marketplace = await marketplace.getAddress();
  addresses.bidSystem = await marketplace.bidSystem();
  
  // 6. Integration
  const BlocTimeIntegration = await hre.ethers.getContractFactory('BlocTimeIntegration');
  const integration = await BlocTimeIntegration.deploy(
    addresses.marketplace,
    addresses.registry,
    addresses.staking
  );
  await integration.waitForDeployment();
  addresses.integration = await integration.getAddress();
  
  // Health check
  const [mHealthy, rHealthy, sHealthy, status] = await integration.healthCheck();
  console.log('Health check:', { mHealthy, rHealthy, sHealthy, status });
  
  return addresses;
}

// Usage
const addresses = await deployBlocTime({
  tokenName: 'Native Token',
  tokenSymbol: 'NAT',
  tokenSupply: ethers.parseEther('1000000'),
  blocTimeName: 'BlocTime Token',
  blocTimeSymbol: 'BLOC',
  maxLockBlocks: 100000,
  distributionPercentage: 5000,
  treasuryFeeBps: 250,
  multiplierPoints: [
    { blocks: 0, multiplier: 10000 },
    { blocks: 10000, multiplier: 15000 },
    { blocks: 50000, multiplier: 20000 },
    { blocks: 100000, multiplier: 30000 }
  ]
});

console.log('Deployment complete:', addresses);
```

---

## Support

For integration support:
- GitHub Issues: [repository]/issues
- Documentation: /docs
- API Reference: /docs/API_REFERENCE.md
