# BlocTime Protocol - API Reference

## Smart Contract APIs

### BlocTimeStaking

#### Core Functions

##### `stake(uint256 amount, uint256 lockBlocks)`
Stake native tokens to earn BlocTime tokens with multiplier.

**Parameters:**
- `amount`: Amount of native tokens to stake
- `lockBlocks`: Number of blocks to lock (0 to maxLockBlocks)

**Returns:** None

**Events:** `Staked(address indexed user, uint256 amount, uint256 lockBlocks, uint256 blocTimeEarned)`

**Example:**
```javascript
// Stake 1000 tokens for 50k blocks (2x multiplier)
await nativeToken.approve(stakingAddress, ethers.parseEther('1000'));
await staking.stake(ethers.parseEther('1000'), 50000);
```

##### `unstake()`
Unstake tokens after lock period expires.

**Requirements:**
- Lock period must be complete
- Must have active stake

**Events:** `Unstaked(address indexed user, uint256 amount, uint256 blocTimeReturned)`

##### `claimRewards()`
Claim pending treasury rewards without unstaking.

**Returns:** None

**Events:** `RewardsClaimed(address indexed user, uint256 amount)`

##### `fundTreasury(uint256 amount)`
Fund the treasury (called by marketplace automatically).

**Parameters:**
- `amount`: Amount to add to treasury

**Events:** `TreasuryFunded(uint256 amount)`

#### View Functions

##### `getStakeInfo(address user)`
Get complete stake information for a user.

**Returns:**
- `amount`: Staked token amount
- `startBlock`: Block when stake started
- `lockBlocks`: Total lock duration
- `blocTimeBalance`: BlocTime tokens earned
- `blocksRemaining`: Blocks until unlock
- `rewards`: Pending claimable rewards

##### `pendingRewards(address user)`
Calculate pending rewards for user.

**Returns:** `uint256` - Claimable reward amount

**Formula:**
```
rewards = (userBlocTime / totalBlocTime) * treasury * distributionPercentage
```

##### `getMultiplier(uint256 blockCount)`
Get multiplier for given lock duration.

**Returns:** `uint256` - Multiplier in basis points (10000 = 1x)

##### `getAllPoints()`
Get all multiplier curve points.

**Returns:** `Point[]` - Array of (blocks, multiplier) points

#### Admin Functions

##### `setPoints(Point[] calldata _points)`
Set multiplier curve points (owner only).

**Requirements:**
- Points must be monotonically increasing
- Multipliers must be >= 10000 (1x)

##### `setMaxLockBlocks(uint256 _maxLockBlocks)`
Update maximum lock duration (owner only).

##### `setDistributionPercentage(uint256 _percentage)`
Set treasury distribution percentage (owner only).

**Parameters:**
- `_percentage`: Percentage in basis points (max 10000 = 100%)

---

### BlocTimeMarketplaceMultiToken

#### Core Functions

##### `rent(uint256 moduleId, uint256 blocks, address paymentToken)`
Rent a module for specified blocks.

**Parameters:**
- `moduleId`: ID of module to rent
- `blocks`: Number of blocks to rent
- `paymentToken`: ERC20 token for payment (must be whitelisted)

**Returns:** `uint256` - Rental ID

**Events:** `Rented(uint256 indexed rentalId, uint256 indexed moduleId, address indexed renter, uint256 blocks, uint256 cost, address paymentToken)`

**Example:**
```javascript
const cost = pricePerBlock * blocks;
await paymentToken.approve(marketplaceAddress, cost);
const rentalId = await marketplace.rent(moduleId, 1000, paymentTokenAddress);
```

##### `listFractionalForSale(uint256 rentalId, uint256 fromBlock, uint256 toBlock, uint256 price, address paymentToken)`
List unused rental blocks for sale.

**Parameters:**
- `rentalId`: Your rental ID
- `fromBlock`: Start block of range (relative to rental start)
- `toBlock`: End block of range
- `price`: Listing price
- `paymentToken`: Payment token for listing

**Returns:** `uint256` - Listing ID

**Requirements:**
- Must own rental
- No overlapping listings
- Range must be within paid blocks

##### `buy(uint256 listingId)`
Purchase a fractional listing.

**Parameters:**
- `listingId`: ID of listing to purchase

**Events:** `Sold(uint256 indexed listingId, address indexed buyer, uint256 price, address paymentToken)`

##### `endRental(uint256 rentalId)`
End rental early and cancel associated listings.

##### `cancelListing(uint256 listingId)`
Cancel your own listing.

#### Bid System Functions

##### `acceptBid(uint256 bidId)`
Accept a bid on your rental slot.

**Requirements:**
- Must own the rental
- Bid must be active

##### `rejectBid(uint256 bidId)`
Reject a bid on your rental slot.

#### View Functions

##### `getRental(uint256 id)`
Get rental details.

**Returns:**
- `renter`: Address of renter
- `moduleId`: Module being rented
- `startBlock`: Rental start block
- `paidBlocks`: Total blocks paid for
- `paymentToken`: Token used for payment
- `active`: Whether rental is active

##### `getListing(uint256 id)`
Get listing details.

##### `getRemainingBlocks(uint256 rentalId)`
Get remaining blocks in rental.

##### `getUserRentals(address user)`
Get all rental IDs for user.

##### `getRentalListings(uint256 rentalId)`
Get all listing IDs for rental.

##### `hasOverlappingListing(uint256 rentalId, uint256 fromBlock, uint256 toBlock)`
Check if block range overlaps existing listings.

---

### BlocTimeRegistry

#### Core Functions

##### `registerModule(uint256 pricePerBlock, uint256 maxUsers, string memory ipfsHash)`
Register a new module.

**Parameters:**
- `pricePerBlock`: Price per block in payment token
- `maxUsers`: Maximum concurrent users
- `ipfsHash`: IPFS hash of module metadata

**Returns:** `uint256` - Module ID

**Events:** `ModuleRegistered(uint256 indexed moduleId, address indexed owner, uint256 pricePerBlock)`

##### `updateModule(uint256 moduleId, uint256 pricePerBlock, uint256 maxUsers)`
Update module parameters (owner only).

##### `deactivateModule(uint256 moduleId)`
Deactivate module (owner only).

##### `incrementUsers(uint256 moduleId)`
Increment user count (called by marketplace).

##### `decrementUsers(uint256 moduleId)`
Decrement user count (called by marketplace).

#### View Functions

##### `getModule(uint256 id)`
Get module details.

**Returns:**
- `owner`: Module owner address
- `pricePerBlock`: Price per block
- `maxConcurrentUsers`: Maximum users
- `currentUsers`: Current user count
- `active`: Whether module is active
- `ipfsHash`: Metadata IPFS hash

##### `isModuleAvailable(uint256 moduleId)`
Check if module is available for rent.

##### `getUserModules(address user)`
Get all module IDs owned by user.

---

### PaymentTokenWhitelist

#### Admin Functions

##### `whitelistToken(address token)`
Add token to whitelist (owner only).

**Requirements:**
- Must be valid ERC20
- Not already whitelisted

##### `delistToken(address token)`
Remove token from whitelist (owner only).

#### View Functions

##### `isTokenWhitelisted(address token)`
Check if token is whitelisted.

##### `getWhitelistedTokens()`
Get all whitelisted token addresses.

##### `getWhitelistedTokenCount()`
Get count of whitelisted tokens.

---

### BlocTimeBidSystem

#### Core Functions

##### `createBid(uint256 rentalId, uint256 fromBlock, uint256 toBlock, uint256 bidAmount, address paymentToken)`
Create bid on rental slot.

**Parameters:**
- `rentalId`: Target rental ID
- `fromBlock`: Start block of desired range
- `toBlock`: End block of desired range
- `bidAmount`: Bid amount (locked in escrow)
- `paymentToken`: Payment token (must be whitelisted)

**Returns:** `uint256` - Bid ID

##### `cancelBid(uint256 bidId)`
Cancel your own bid and get refund.

#### View Functions

##### `getBid(uint256 bidId)`
Get bid details.

##### `getRentalBids(uint256 rentalId)`
Get all bid IDs for rental.

##### `getUserBids(address user)`
Get all bid IDs by user.

---

### BlocTimeIntegration

#### View Functions

##### `healthCheck()`
Comprehensive system health check.

**Returns:**
- `marketplaceHealthy`: Boolean
- `registryHealthy`: Boolean
- `stakingHealthy`: Boolean
- `status`: String description

##### `validateModuleRegistration(uint256 moduleId)`
Validate module registration.

**Returns:**
- `valid`: Boolean
- `reason`: String explanation

##### `validateRentalFlow(uint256 rentalId)`
Validate rental end-to-end.

##### `getSystemStats()`
Get comprehensive system statistics.

**Returns:**
- `totalModules`: Total registered modules
- `totalRentals`: Total rentals created
- `totalStaked`: Total tokens staked
- `totalBlocTime`: Total BlocTime in circulation
- `treasuryBalance`: Current treasury balance

---

## JavaScript SDK Examples

### Setup

```javascript
const { ethers } = require('hardhat');

// Load contracts
const staking = await ethers.getContractAt('BlocTimeStaking', STAKING_ADDRESS);
const marketplace = await ethers.getContractAt('BlocTimeMarketplaceMultiToken', MARKETPLACE_ADDRESS);
const registry = await ethers.getContractAt('BlocTimeRegistry', REGISTRY_ADDRESS);
```

### Complete Staking Flow

```javascript
// 1. Approve tokens
const amount = ethers.parseEther('1000');
await nativeToken.approve(stakingAddress, amount);

// 2. Stake with 50k block lock (2x multiplier)
await staking.stake(amount, 50000);

// 3. Check stake info
const info = await staking.getStakeInfo(userAddress);
console.log('BlocTime earned:', ethers.formatEther(info.blocTimeBalance));
console.log('Pending rewards:', ethers.formatEther(info.rewards));

// 4. Claim rewards anytime
await staking.claimRewards();

// 5. After lock period, unstake
await staking.unstake();
```

### Complete Marketplace Flow

```javascript
// 1. Register module
const pricePerBlock = ethers.parseEther('0.01');
const moduleId = await registry.registerModule(
  pricePerBlock,
  10, // max users
  'QmModuleMetadata123'
);

// 2. Rent module
const blocks = 1000;
const cost = pricePerBlock * BigInt(blocks);
await paymentToken.approve(marketplaceAddress, cost);
const rentalId = await marketplace.rent(moduleId, blocks, paymentTokenAddress);

// 3. List unused blocks
await marketplace.listFractionalForSale(
  rentalId,
  600, // from block
  1000, // to block
  ethers.parseEther('3'), // price
  paymentTokenAddress
);

// 4. Buy listing
const listingPrice = ethers.parseEther('3');
await paymentToken.approve(marketplaceAddress, listingPrice);
await marketplace.buy(listingId);
```

### Bid System Flow

```javascript
// 1. Create bid
const bidAmount = ethers.parseEther('5');
await paymentToken.approve(bidSystemAddress, bidAmount);
const bidId = await bidSystem.createBid(
  rentalId,
  100, // from block
  500, // to block
  bidAmount,
  paymentTokenAddress
);

// 2. Rental owner accepts bid
await marketplace.acceptBid(bidId);

// OR cancel bid
await bidSystem.cancelBid(bidId);
```

---

## Error Codes

### Staking Errors
- `"Amount must be > 0"` - Zero stake amount
- `"Exceeds max lock blocks"` - Lock duration too long
- `"Already staking"` - User already has active stake
- `"No active stake"` - No stake to unstake
- `"Still locked"` - Lock period not complete
- `"No rewards available"` - No pending rewards
- `"Insufficient treasury"` - Treasury cannot cover rewards

### Marketplace Errors
- `"Token not whitelisted"` - Payment token not allowed
- `"Module unavailable"` - Module not available for rent
- `"Invalid blocks"` - Zero or invalid block count
- `"Not renter"` - Not the rental owner
- `"Rental not active"` - Rental has ended
- `"Invalid block range"` - fromBlock >= toBlock
- `"Range exceeds paid blocks"` - Listing range too large
- `"Overlapping listing exists"` - Block range conflicts
- `"Listing not active"` - Listing already sold/cancelled

### Registry Errors
- `"Invalid price"` - Zero price per block
- `"Invalid max users"` - Zero max users
- `"Invalid IPFS hash"` - Empty metadata hash
- `"Not module owner"` - Not authorized
- `"Module not active"` - Module deactivated
- `"Max users below current"` - Cannot reduce below current users
- `"Max users reached"` - Module at capacity

---

## Gas Estimates

| Operation | Estimated Gas | Notes |
|-----------|--------------|-------|
| `stake()` | ~150,000 | First stake costs more |
| `unstake()` | ~100,000 | Burns BlocTime tokens |
| `claimRewards()` | ~80,000 | Simple transfer |
| `rent()` | ~200,000 | Includes treasury funding |
| `listFractionalForSale()` | ~120,000 | Creates listing |
| `buy()` | ~180,000 | Creates new rental |
| `registerModule()` | ~150,000 | First module costs more |
| `createBid()` | ~130,000 | Locks tokens in escrow |
| `acceptBid()` | ~200,000 | Creates rental |

---

## Events Reference

### Staking Events
```solidity
event Staked(address indexed user, uint256 amount, uint256 lockBlocks, uint256 blocTimeEarned);
event Unstaked(address indexed user, uint256 amount, uint256 blocTimeReturned);
event TreasuryFunded(uint256 amount);
event RewardsClaimed(address indexed user, uint256 amount);
event PointsSet(uint256 pointCount);
```

### Marketplace Events
```solidity
event Rented(uint256 indexed rentalId, uint256 indexed moduleId, address indexed renter, uint256 blocks, uint256 cost, address paymentToken);
event ListedFractional(uint256 indexed listingId, uint256 indexed rentalId, uint256 fromBlock, uint256 toBlock, uint256 price, address paymentToken);
event Sold(uint256 indexed listingId, address indexed buyer, uint256 price, address paymentToken);
event RentalEnded(uint256 indexed rentalId);
```

### Registry Events
```solidity
event ModuleRegistered(uint256 indexed moduleId, address indexed owner, uint256 pricePerBlock);
event ModuleUpdated(uint256 indexed moduleId, uint256 pricePerBlock, uint256 maxUsers);
event ModuleDeactivated(uint256 indexed moduleId);
event UserCountChanged(uint256 indexed moduleId, uint256 currentUsers);
```

### Bid System Events
```solidity
event BidCreated(uint256 indexed bidId, uint256 indexed rentalId, address indexed bidder, uint256 fromBlock, uint256 toBlock, uint256 amount, address paymentToken);
event BidAccepted(uint256 indexed bidId, address indexed slotOwner);
event BidRejected(uint256 indexed bidId, address indexed slotOwner);
event BidCancelled(uint256 indexed bidId, address indexed bidder);
```
