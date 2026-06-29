# Smart Contracts — BlocTime Protocol

The BlocTime Protocol is a suite of Solidity smart contracts deployed on Base Sepolia. It provides revenue distribution, marketplace credits, time-weighted staking, module registration, and access control — all on-chain.

## Tech Stack

- **Solidity** 0.8.20
- **Hardhat** (compile, test, deploy)
- **OpenZeppelin** (ERC20, ReentrancyGuard, Ownable, Pausable, SafeERC20)
- **Network**: Base Sepolia testnet (chainId `84532`)
- **RPC**: `https://sepolia.base.org`

## Deployed Contracts (Base Sepolia)

| Contract | Address |
|----------|---------|
| USDC | `0xe22970F0bB899C7D615ED522B2A807629F99ec01` |
| USDT | `0xc68d5E71404cAb1101597B7531A5738873E226Bc` |
| NativeToken | `0xB9b6F5CdB25f8BC9fC88CA171381B509Df907b51` |
| ManualPriceOracle | `0x40C37CA1321f967831c86E5AF8935aC043F9adF1` |
| TokenGate | `0x97c7a7066e80F13Ee4ABEdeaA223CbC71472de8b` |
| BlocTime | `0xF25AAFDd0A842ff50b041595C79210b48d6795bD` |
| Registry | `0x4f9e72C935e5762E941F98DA50696cb022008a43` |
| Treasury | `0xe9a96Ae58108E9Dd7e14c5DdCb66C175BB877785` |
| Market | `0x2F0B61616Fbf662A4f4C544D7d5d909D74ef7687` |
| Debit | `0x6F941E762C7Df3db8DfD0C47d53Acd85D73Da442` |

Config: `mod/core/chain/config.json`
ABIs: Stored on IPFS (referenced in config)

## Contracts

### Treasury

**Purpose**: Distribute revenue proportionally to governance token holders.

How it works:
- Anyone deposits whitelisted tokens via `fundTreasury(token, amount)`
- Each governance token holder can claim their proportional share
- Owner gets a configurable percentage (basis points, 10000 = 100%)
- Distribution is based on **current balance** — no historical accounting

**Key functions**:
```solidity
fundTreasury(address token, uint256 amount)         // Deposit tokens
getClaimableAmount(address holder, address token)    // Check your share
withdrawToken(address token)                         // Claim your share
withdrawAll()                                        // Claim from all tokens
ownerWithdraw(address token)                         // Owner claims reserved %
setOwnerless()                                       // Renounce ownership forever
```

**Share calculation**:
```
holderShare = (treasuryBalance * (10000 - ownerPercentage) * holderTokenBalance) / (10000 * totalTokenSupply)
```

---

### Market

**Purpose**: Credit/debit system for stable USD tokens with oracle-backed price conversion.

How it works:
- Users deposit payment tokens (USDC/USDT) and receive Market tokens (8 decimals, USD-pegged)
- Oracle adapters provide real-time price feeds for conversion
- Credits and withdrawals have configurable fees (sent to Treasury)
- A separate Debit contract handles client-provider debits (unsigned or EIP-712 signed)

**Key functions**:
```solidity
credit(address paymentToken, uint256 stableAmount, uint256 maxPaymentAmount)
withdraw(address paymentToken, uint256 stableAmount, uint256 minReceiveAmount)
debit(address client, address provider, uint256 stableAmount, uint256 deadline, bytes signature)
```

**Fees**:
- Credit fee: configurable (`creditFeeBps`, default 100 = 1%)
- Treasury fee: 5% constant on debits

---

### BlocTime

**Purpose**: Staking with time-weighted multiplier rewards.

How it works:
- Users stake the NativeToken for a chosen lock period (in blocks)
- A multiplier curve determines the reward multiplier based on lock duration
- BlocTime tokens are minted: `amount * multiplier / 10000`
- After the lock period, users unstake to get their NativeToken back (BlocTime burned)

**Key functions**:
```solidity
stake(uint256 amount, uint256 lockBlocks)           // Stake tokens
unstake(uint256 stakeId)                             // Unstake after lock
getMultiplier(uint256 blockCount)                    // Query multiplier curve
getUserStakeIds(address user)                        // List stake positions
getStakePosition(address user, uint256 stakeId)      // Position details
setPoints(Point[] calldata points)                   // Set multiplier curve (owner)
```

**Multiplier curve**: Linear interpolation between configured points. Example:
- 0 blocks → 1x (10000 bps)
- 10,000 blocks → 2x (20000 bps)
- 100,000 blocks → 5x (50000 bps)

---

### TokenGate

**Purpose**: Token whitelist and oracle adapter registry.

How it works:
- Maintains a list of approved tokens that can be used across the protocol
- Each token can have its own oracle adapter, or fall back to the default
- Used by Market and Treasury to validate tokens before accepting deposits

**Key functions**:
```solidity
whitelistToken(address token)                        // Add token
batchWhitelistTokens(address[] tokens)               // Bulk add
delistToken(address token)                           // Remove token
setDefaultOracle(address oracle)                     // Set fallback oracle
registerTokenOracle(address token, address oracle)   // Token-specific oracle
getTokenPrice(address token)                         // → (price, decimals, timestamp)
```

**Oracle adapters** (implement `IOracleAdapter`):
- `ManualPriceOracle` — admin-set prices
- `ChainlinkAdapter` — Chainlink price feeds
- `PythAdapter` — Pyth Network feeds

---

### Registry

**Purpose**: On-chain module registry with IPFS metadata.

How it works:
- Users register modules with a name and IPFS hash (metadata)
- Names are unique per creator (not globally)
- Supports updates, transfers, and deletion

**Key functions**:
```solidity
registerMod(string name, string data)                // Register → returns modId
updateMod(uint256 modId, string data)                // Update metadata
removeMod(uint256 modId)                             // Delete
transferOwnership(uint256 modId, address newOwner)   // Transfer
getMod(uint256 id)                                   // Get mod details
getUserMods(address user)                            // List user's mods
```

---

### Perms

**Purpose**: Hierarchical key-based permission system.

How it works:
- Parent keys map to arrays of child keys (arbitrary bytes)
- First person to add children to a parent key becomes its owner
- Owners can add/remove children, transfer ownership
- Configurable limits on child count and key size

**Key functions**:
```solidity
addKey(bytes parentKey, bytes childKey)              // Add child key
removeKey(bytes parentKey, bytes childKey)            // Remove child key
setKeys(bytes parentKey, bytes[] childKeys)           // Replace all children
getKeys(bytes parentKey)                              // List children
getKeyOwner(bytes parentKey)                          // Get owner
transferKeyOwnership(bytes parentKey, address newOwner)
```

---

### Safe (Gnosis Safe)

**Purpose**: Multi-signature wallet for team/DAO treasury management.

Contracts:
- `Safe.sol` — Core multisig logic
- `SafeProxy.sol` — Minimal proxy for cheap deployment
- `SafeProxyFactory.sol` — Factory for creating Safe instances

**Important gotchas**:
- The Safe Transaction Service only recognizes canonical Safe Global deployments. Custom-deployed Safes (own singleton + factory) will always fail hash verification with the service.
- For custom deployments, use client-side signature aggregation: collect signatures in localStorage until threshold is met, then call `execTransaction()` on-chain.
- Signatures require `v + 4` adjustment: Safe's `checkNSignatures` uses `v > 30` to detect `eth_sign` mode. It subtracts 4 from `v`, then wraps the hash with `\x19Ethereum Signed Message:\n32` before `ecrecover`. Without the `+4`, Safe uses raw ECDSA → wrong recovered address → "Invalid signature order".

## Development

### Setup

```bash
cd mod/core/chain
npm install
```

### Compile

```bash
npx hardhat compile
```

### Deploy

```bash
npx hardhat run scripts/deploy.js --network testnet
```

### Hardhat Config

Networks configured in `hardhat.config.js`:

| Network | Chain ID | RPC |
|---------|----------|-----|
| ganache | 1337 | `localhost:8545` |
| testnet | 84532 | `https://sepolia.base.org` |
| mainnet | 8453 | `https://mainnet.base.org` |

Solidity: v0.8.20, optimizer enabled (200 runs)
