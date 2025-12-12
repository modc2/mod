# BlocStaking V2 - Modular Architecture

## 🚀 Overview

BlocStaking V2 is a fully modular, upgradable staking system built with the diamond storage pattern for maximum flexibility and future extensibility.

## 📁 Structure

```
bloc/
├── interfaces/
│   └── IBlocStaking.sol          # Core interface definitions
├── libraries/
│   └── BlocStorage.sol           # Diamond storage pattern implementation
├── modules/
│   ├── BlocRegistry.sol          # Bloc registration & management
│   ├── StakingModule.sol         # Staking logic
│   ├── AccessModule.sol          # Exclusive access management
│   └── AdminModule.sol           # Admin functions & treasury
└── BlocStakingV2.sol             # Main contract orchestrator
```

## 🎯 Key Features

### Modularity
- **Separation of Concerns**: Each module handles specific functionality
- **Easy Testing**: Test modules independently
- **Maintainability**: Update individual modules without touching others

### Upgradeability
- **Diamond Storage Pattern**: Prevents storage collisions
- **Future-Proof**: Add new modules without redeploying
- **Proxy-Ready**: Can be integrated with proxy patterns

### Security
- **ReentrancyGuard**: Protection on all state-changing functions
- **Access Control**: Owner-only admin functions
- **SafeERC20**: Safe token transfers

## 🔧 Modules

### BlocRegistry
Handles all bloc lifecycle operations:
- Register new blocs
- Update bloc metadata
- Transfer ownership
- Deregister blocs

### StakingModule
Manages staking operations:
- Stake tokens to blocs
- Unstake tokens
- Track stake info

### AccessModule
Controls exclusive access:
- Purchase time-based access slots
- Multi-user concurrent access
- Automatic slot cleanup
- Access verification

### AdminModule
Administrative functions:
- Configure pricing
- Set access parameters
- Treasury management

## 🎨 Usage

```solidity
// Deploy
BlocStakingV2 staking = new BlocStakingV2(
    tokenAddress,
    pricePerInterval,
    intervalDuration,
    maxConcurrentUsers
);

// Register a bloc
uint256 blocId = staking.registerBloc("QmIPFSHash...");

// Stake tokens
token.approve(address(staking), amount);
staking.stake(blocId, amount);

// Purchase exclusive access
staking.purchaseExclusiveAccess(blocId, intervals);
```

## 🔮 Future Extensions

Easy to add:
- Rewards module
- Governance module
- NFT integration
- Advanced access tiers
- Analytics module

## 🛡️ Security

- All modules use internal functions
- Main contract acts as gatekeeper
- Diamond storage prevents collisions
- Comprehensive event logging

## 📊 Gas Optimization

- Library pattern reduces deployment costs
- Efficient storage layout
- Minimal external calls
- Batch operations support

---

**Built with 💎 for maximum flexibility and future growth**
