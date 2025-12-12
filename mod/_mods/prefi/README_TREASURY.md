# Treasury Prediction Market with Tokenomics

## Overview

Advanced prediction market that calculates profits in USD, locks a percentage into treasury, and mints/burns tokens based on trade performance. Fully integrated with Uniswap V3.

## Key Features

- ✅ **USD Profit Calculation**: All profits calculated in USD value
- ✅ **Treasury Allocation**: Configurable % of profits locked for protocol use
- ✅ **Token Minting**: Mint tokens to users on profitable trades
- ✅ **Token Burning**: Burn tokens from users on losing trades
- ✅ **Uniswap V3 Integration**: Real-time price feeds
- ✅ **Super Modular**: Easy to configure and extend

## Architecture

### Flow Diagram

```
1. User opens position → Track USD value at entry
2. Position settles → Calculate USD profit/loss
3. If profitable:
   - Lock N% to treasury
   - Distribute (100-N)% to winners
   - Mint tokens proportional to USD profit
4. If loss:
   - Burn tokens proportional to USD loss
```

## Smart Contract

### Key Functions

```solidity
// Open position with USD tracking
function openPosition(
    uint256 amount,
    Direction direction,
    address token0,
    address token1,
    uint24 poolFee
) external returns (bytes32)

// Settle with token minting/burning
function settlePosition(bytes32 positionId) external

// Distribute with treasury allocation
function distributeRewards(address token0, address token1) external

// Get USD value of any token
function getUSDValue(address token, uint256 amount) public view returns (uint256)

// Calculate profit in USD
function calculateProfitUSD(bytes32 positionId) public view returns (uint256)
```

### Configuration

```solidity
// Set treasury percentage (0-50%)
function setTreasuryPercentage(uint256 _percentage) external

// Set treasury address
function setTreasury(address _treasury) external

// Set USD token (e.g., USDC)
function setUsdToken(address _usdToken) external
```

## Python Module Usage

### Basic Setup

```python
from prefi.treasury_prediction_mod import TreasuryPredictionMod

# Initialize with treasury config
mod = TreasuryPredictionMod({
    'uniswap_factory': '0x1F98431c8aD98523631AE4a59f267346ea31F984',
    'uniswap_router': '0xE592427A0AEce92De3Edee1F18E0157C05861564',
    'uniswap_quoter': '0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6',
    'treasury_address': '0xYourTreasuryAddress',
    'treasury_percentage': 10,  # 10% to treasury
    'reward_pool_percentage': 90,  # 90% to winners
    'usd_token': '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48',  # USDC
    'token_mint_ratio': 1000,  # 1000 tokens per $1 profit
    'token_burn_ratio': 1000   # 1000 tokens burned per $1 loss
})
```

### Open Position

```python
# Deposit funds
mod.execute(
    action='deposit',
    user_address='0x123...',
    amount=1.0
)

# Open position (auto-tracks USD value)
result = mod.execute(
    action='open_position',
    user_address='0x123...',
    amount=0.5,
    direction='up',
    token0='0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2',  # WETH
    token1='0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48',  # USDC
    fee=3000
)

print(f"Position ID: {result['position_id']}")
print(f"USD Value at Entry: ${result['usd_value_at_entry']:.2f}")
```

### Settle Position with Tokenomics

```python
# Settle position (auto-calculates USD profit/loss)
result = mod.execute(
    action='settle_position',
    position_id='abc123...',
    token0='0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2',
    token1='0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48',
    fee=3000
)

if result['profitable']:
    print(f"Profit: ${result['profit_usd']:.2f}")
    print(f"Tokens Minted: {result['tokens_minted']:.0f}")
    print(f"New Token Balance: {result['new_token_balance']:.0f}")
else:
    print(f"Loss: ${result['loss_usd']:.2f}")
    print(f"Tokens Burned: {result['tokens_burned']:.0f}")
    print(f"Remaining Tokens: {result['new_token_balance']:.0f}")
```

### Distribute Rewards with Treasury

```python
# Distribute rewards (auto-allocates to treasury)
result = mod.execute(
    action='distribute_rewards',
    asset='WETH/USDC',
    settlement_time='2024-01-01T00:00:00'
)

print(f"Winners: {result['winners']}")
print(f"Total Distributed: ${result['total_distributed']:.2f}")
print(f"Treasury Allocation: ${result['treasury_allocation']:.2f}")
print(f"Treasury Balance: ${result['total_treasury_balance']:.2f}")
```

### Check Balances

```python
# Get user's token balance
result = mod.execute(
    action='get_token_balance',
    user_address='0x123...'
)
print(f"Token Balance: {result['token_balance']:.0f}")

# Get treasury balance
result = mod.execute(action='get_treasury_balance')
print(f"Treasury: ${result['treasury_balance']:.2f}")
```

### Get USD Price

```python
# Get current USD value
result = mod.execute(
    action='get_usd_price',
    token0='0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2',  # WETH
    token1='0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48',  # USDC
    amount=1.0,
    fee=3000
)
print(f"1 WETH = ${result['usd_value']:.2f}")
```

## Example: Complete Trading Flow

```python
from prefi.treasury_prediction_mod import TreasuryPredictionMod

# Initialize
mod = TreasuryPredictionMod({
    'treasury_percentage': 15,  # 15% to treasury
    'token_mint_ratio': 100,    # 100 tokens per $1 profit
    'token_burn_ratio': 100
})

# User deposits $1000
mod.execute(action='deposit', user_address='0xAlice', amount=1000)

# Alice predicts ETH will go UP
result = mod.execute(
    action='open_position',
    user_address='0xAlice',
    amount=500,  # $500 position
    direction='up',
    token0='0xWETH',
    token1='0xUSDC',
    fee=3000
)
position_id = result['position_id']
print(f"Entry USD Value: ${result['usd_value_at_entry']}")

# ... 24 hours later, ETH went from $2000 to $2100 ...

# Settle position
result = mod.execute(
    action='settle_position',
    position_id=position_id,
    token0='0xWETH',
    token1='0xUSDC',
    fee=3000
)

if result['profitable']:
    # Alice made $25 profit (5% gain on $500)
    # Minted: 25 * 100 = 2,500 tokens
    print(f"Profit: ${result['profit_usd']}")
    print(f"Tokens Minted: {result['tokens_minted']}")

# Distribute rewards
result = mod.execute(
    action='distribute_rewards',
    asset='WETH/USDC',
    settlement_time='2024-01-01T00:00:00'
)

# 15% of losing pool goes to treasury
print(f"Treasury got: ${result['treasury_allocation']}")
print(f"Winners got: ${result['total_distributed']}")
```

## Tokenomics Model

### Minting Formula

```
Tokens Minted = USD Profit × Mint Ratio

Example:
- User makes $50 profit
- Mint ratio = 1000
- Tokens minted = 50 × 1000 = 50,000 tokens
```

### Burning Formula

```
Tokens Burned = min(USD Loss × Burn Ratio, User's Token Balance)

Example:
- User loses $30
- Burn ratio = 1000
- User has 40,000 tokens
- Tokens burned = min(30 × 1000, 40,000) = 30,000 tokens
```

### Treasury Allocation

```
Treasury Amount = Total Losing Pool × Treasury %
Winner Pool = Total Losing Pool × (100 - Treasury %)

Example:
- Total losing pool = $1000
- Treasury % = 10%
- Treasury gets: $100
- Winners share: $900
```

## Configuration Options

| Parameter | Default | Description |
|-----------|---------|-------------|
| `treasury_percentage` | 10 | % of profits to treasury (0-50) |
| `reward_pool_percentage` | 90 | % distributed to winners |
| `token_mint_ratio` | 1000 | Tokens minted per $1 profit |
| `token_burn_ratio` | 1000 | Tokens burned per $1 loss |
| `usd_token` | USDC | Stablecoin for USD calculation |
| `prediction_duration_hours` | 24 | Position duration |

## Deployment

### Deploy Contract

```bash
npm install @openzeppelin/contracts
node deploy_treasury_market.js --network base
```

### Constructor Parameters

```javascript
const contract = await TreasuryPredictionMarket.deploy(
    uniswapFactory,
    swapRouter,
    quoter,
    treasuryAddress,
    usdTokenAddress,  // USDC
    "Prediction Token",
    "PRED"
);
```

## Security Considerations

- ✅ Treasury percentage capped at 50%
- ✅ Token burning limited to user's balance
- ✅ USD price feeds from trusted Uniswap V3 pools
- ✅ Modular design for easy auditing
- ⚠️ Audit before production use

## Advanced Features

### Dynamic Treasury Allocation

```python
# Adjust treasury percentage based on market conditions
if market_volatility > 0.5:
    mod.config['treasury_percentage'] = 20  # Higher treasury in volatile markets
else:
    mod.config['treasury_percentage'] = 10
```

### Token Buyback Mechanism

```python
# Use treasury funds to buy back tokens
treasury_balance = mod.execute(action='get_treasury_balance')['treasury_balance']
if treasury_balance > 10000:
    # Implement buyback logic
    pass
```

## License

MIT License

---

**Built for sustainable DeFi with treasury management and tokenomics** 🚀💰
