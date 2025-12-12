# Ultimate Prediction Market - Super Modular Edition

## Overview

The most advanced, modular prediction market with:
- ✅ **Uniswap V3 Integration** - Real-time price feeds
- ✅ **USD Profit Calculation** - All profits calculated in USD value
- ✅ **Treasury Allocation** - Lock N% of profits for protocol use
- ✅ **Token Minting** - Mint tokens on profitable trades (proportional to USD profit)
- ✅ **Token Burning** - Burn tokens on losing trades (proportional to USD loss)
- ✅ **Super Modular** - Configure everything at runtime

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                  ULTIMATE PREDICTION MARKET                  │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │  Uniswap V3  │  │  USD Oracle  │  │   Treasury   │      │
│  │ Price Feeds  │  │  Calculator  │  │   Manager    │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
│                                                               │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │    Token     │  │  Prediction  │  │   Reward     │      │
│  │ Mint/Burn    │  │    Market    │  │ Distribution │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

## Complete Trading Flow

### 1. User Opens Position
```python
from prefi.ultimate_prediction_mod import UltimatePredictionMod

# Initialize with custom config
mod = UltimatePredictionMod({
    'treasury_percentage': 15,      # 15% to treasury
    'token_mint_ratio': 100,        # 100 tokens per $1 profit
    'token_burn_ratio': 100,        # 100 tokens burned per $1 loss
    'prediction_duration_hours': 24
})

# Deposit funds
mod.execute(
    action='deposit',
    user_address='0xAlice',
    amount=1000.0  # $1000
)

# Open position - predicts ETH will go UP
result = mod.execute(
    action='open_position',
    user_address='0xAlice',
    amount=500.0,  # $500 position
    direction='up',
    token0='0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2',  # WETH
    token1='0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48',  # USDC
    fee=3000
)

print(f"Position ID: {result['position_id']}")
print(f"USD Value at Entry: ${result['usd_value_at_entry']:.2f}")
print(f"Entry Price: {result['entry_price']}")
```

### 2. Position Settles (After 24 Hours)

**Scenario A: Profitable Trade (ETH went UP)**
```python
# ETH went from $2000 to $2100 (+5%)
result = mod.execute(
    action='settle_position',
    position_id='abc123...',
    token0='0xWETH',
    token1='0xUSDC',
    fee=3000
)

if result['profitable']:
    # User made $25 profit (5% of $500)
    print(f"✅ PROFITABLE TRADE")
    print(f"Profit: ${result['profit_usd']:.2f}")  # $25
    print(f"Tokens Minted: {result['tokens_minted']:.0f}")  # 2,500 tokens
    print(f"New Token Balance: {result['new_token_balance']:.0f}")
    print(f"Action: {result['action']}")  # MINTED
```

**Scenario B: Losing Trade (ETH went DOWN)**
```python
# ETH went from $2000 to $1900 (-5%)
result = mod.execute(
    action='settle_position',
    position_id='xyz789...',
    token0='0xWETH',
    token1='0xUSDC',
    fee=3000
)

if not result['profitable']:
    # User lost $25 (5% of $500)
    print(f"❌ LOSING TRADE")
    print(f"Loss: ${result['loss_usd']:.2f}")  # $25
    print(f"Tokens Burned: {result['tokens_burned']:.0f}")  # 2,500 tokens
    print(f"Remaining Tokens: {result['new_token_balance']:.0f}")
    print(f"Action: {result['action']}")  # BURNED
```

### 3. Distribute Rewards with Treasury
```python
result = mod.execute(
    action='distribute_rewards',
    asset='WETH/USDC',
    settlement_time='2024-01-01T00:00:00'
)

print(f"Winners: {result['winners']}")
print(f"Total Distributed to Winners: ${result['winner_pool']:.2f}")
print(f"Treasury Allocation (15%): ${result['treasury_allocation']:.2f}")
print(f"Total Treasury Balance: ${result['total_treasury_balance']:.2f}")
```

## Tokenomics Explained

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
- Treasury % = 15%
- Treasury gets: $150
- Winners share: $850
```

## Modular Configuration

### Runtime Configuration Updates
```python
# Update treasury percentage
result = mod.execute(
    action='update_config',
    treasury_percentage=20  # Change to 20%
)

# Update token ratios
result = mod.execute(
    action='update_config',
    token_mint_ratio=500,   # 500 tokens per $1
    token_burn_ratio=500
)

# Update prediction duration
result = mod.execute(
    action='update_config',
    prediction_duration_hours=48  # 48-hour predictions
)

# Get current config
result = mod.execute(action='get_config')
print(result['config'])
```

### Configuration Options

| Parameter | Default | Range | Description |
|-----------|---------|-------|-------------|
| `treasury_percentage` | 10 | 0-50 | % of profits locked to treasury |
| `reward_pool_percentage` | 90 | 50-100 | % distributed to winners |
| `token_mint_ratio` | 1000 | 1-10000 | Tokens minted per $1 profit |
| `token_burn_ratio` | 1000 | 1-10000 | Tokens burned per $1 loss |
| `prediction_duration_hours` | 24 | 1-168 | Position duration in hours |
| `usd_token` | USDC | - | Stablecoin for USD calculation |

## Advanced Usage

### Check USD Price
```python
result = mod.execute(
    action='get_usd_price',
    token0='0xWETH',
    token1='0xUSDC',
    amount=1.0,
    fee=3000
)
print(f"1 WETH = ${result['usd_value']:.2f}")
```

### Check Balances
```python
# ETH balance
result = mod.execute(
    action='get_balance',
    user_address='0xAlice'
)
print(f"ETH Balance: {result['balance']:.4f}")

# Token balance
result = mod.execute(
    action='get_token_balance',
    user_address='0xAlice'
)
print(f"Token Balance: {result['token_balance']:.0f}")

# Treasury balance
result = mod.execute(action='get_treasury_balance')
print(f"Treasury: ${result['treasury_balance']:.2f}")
```

## Smart Contract Deployment

### Deploy to Base
```javascript
const UltimatePredictionMarket = await ethers.getContractFactory("UltimatePredictionMarket");
const contract = await UltimatePredictionMarket.deploy(
    "0x33128a8fC17869897dcE68Ed026d694621f6FDfD",  // Uniswap Factory
    "0x2626664c2603336E57B271c5C0b26F421741e481",  // Swap Router
    "0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a",  // Quoter
    "0xYourTreasuryAddress",                         // Treasury
    "0xUSDCAddress",                                 // USD Token
    "Prediction Token",                              // Token Name
    "PRED"                                           // Token Symbol
);

await contract.deployed();
console.log("Contract deployed to:", contract.address);
```

### Configure Contract
```javascript
// Set treasury percentage
await contract.setTreasuryPercentage(15);  // 15%

// Set token ratios
await contract.setTokenMintRatio(1000);
await contract.setTokenBurnRatio(1000);

// Set prediction duration
await contract.setPredictionDuration(24 * 60 * 60);  // 24 hours
```

## Complete Example: Multi-User Scenario

```python
from prefi.ultimate_prediction_mod import UltimatePredictionMod

# Initialize
mod = UltimatePredictionMod({
    'treasury_percentage': 10,
    'token_mint_ratio': 100
})

# Alice predicts UP
mod.execute(action='deposit', user_address='0xAlice', amount=1000)
alice_pos = mod.execute(
    action='open_position',
    user_address='0xAlice',
    amount=500,
    direction='up',
    token0='0xWETH',
    token1='0xUSDC',
    fee=3000
)

# Bob predicts DOWN
mod.execute(action='deposit', user_address='0xBob', amount=1000)
bob_pos = mod.execute(
    action='open_position',
    user_address='0xBob',
    amount=500,
    direction='down',
    token0='0xWETH',
    token1='0xUSDC',
    fee=3000
)

# ... 24 hours later, ETH went UP from $2000 to $2100 ...

# Settle Alice (winner)
alice_result = mod.execute(
    action='settle_position',
    position_id=alice_pos['position_id'],
    token0='0xWETH',
    token1='0xUSDC',
    fee=3000
)
print(f"Alice: Profit ${alice_result['profit_usd']}, Minted {alice_result['tokens_minted']} tokens")

# Settle Bob (loser)
bob_result = mod.execute(
    action='settle_position',
    position_id=bob_pos['position_id'],
    token0='0xWETH',
    token1='0xUSDC',
    fee=3000
)
print(f"Bob: Loss ${bob_result['loss_usd']}, Burned {bob_result['tokens_burned']} tokens")

# Distribute rewards
dist = mod.execute(
    action='distribute_rewards',
    asset='WETH/USDC',
    settlement_time='2024-01-01T00:00:00'
)
print(f"Treasury got: ${dist['treasury_allocation']}")
print(f"Alice got: ${dist['winner_pool']}")
```

## Security Features

- ✅ Treasury percentage capped at 50%
- ✅ Token burning limited to user's balance
- ✅ USD prices from trusted Uniswap V3 pools
- ✅ Modular design for easy auditing
- ✅ Owner-only configuration updates
- ✅ Reentrancy protection

## Gas Optimization

- Opening position: ~180,000 gas
- Settling position: ~150,000 gas (includes minting/burning)
- Distributing rewards: ~250,000 gas

## License

MIT License

---

**Built for the future of DeFi - Super Modular, USD-based, Treasury-managed, Token-incentivized** 🚀💰🔥
