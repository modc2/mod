# Prediction Market Smart Contract

## Overview

A decentralized 24-hour prediction market smart contract that rewards users for making profitable trades. Users lock liquidity to predict whether an asset will go up or down in the next 24 hours.

## Features

- **24-Hour Lock Period**: Positions are automatically settled after exactly one day
- **Binary Predictions**: Predict UP or DOWN for any asset
- **Automatic Settlement**: Smart contract handles trade execution and exit
- **Reward Distribution**: Profitable traders share the losing pool proportionally
- **Multi-Asset Support**: Works with any asset (BTC, ETH, stocks, etc.)
- **Future Date Support**: Can create positions for any future date
- **User Balances**: Individual balance tracking for each user

## Installation

```python
from prefi.prediction_mod import PredictionMarketMod

# Initialize the prediction market
market = PredictionMarketMod(config={'reward_pool_percentage': 0.95})
```

## Usage

### 1. Deposit Funds

```python
result = market.execute(
    action='deposit',
    user_address='0x123...',
    amount=1000.0
)
```

### 2. Open a Position

```python
result = market.execute(
    action='open_position',
    user_address='0x123...',
    amount=100.0,
    direction='up',  # or 'down'
    current_price=50000.0,
    asset='BTC',
    start_time='2024-01-15T10:00:00'  # Optional, defaults to now
)

# Returns position_id for tracking
position_id = result['position_id']
```

### 3. Settle Position (After 24 Hours)

```python
result = market.execute(
    action='settle_position',
    position_id=position_id,
    exit_price=51000.0,
    current_time='2024-01-16T10:00:00'  # Optional, defaults to now
)

# Returns profit/loss information
print(f"Profitable: {result['profitable']}")
print(f"Profit: {result['profit']}")
```

### 4. Distribute Rewards

```python
result = market.execute(
    action='distribute_rewards',
    asset='BTC',
    settlement_time='2024-01-16T10:00:00'
)

# Returns reward distribution details
print(f"Winners: {result['winners']}")
print(f"Total Distributed: {result['total_distributed']}")
```

### 5. Check Balance

```python
result = market.execute(
    action='get_balance',
    user_address='0x123...'
)

print(f"Balance: {result['balance']}")
```

### 6. View Active Positions

```python
result = market.execute(
    action='get_positions',
    user_address='0x123...'  # Optional, omit for all positions
)

for position in result['positions']:
    print(f"Position: {position['position_id']}")
    print(f"Direction: {position['direction']}")
    print(f"Exit Time: {position['exit_time']}")
```

## How It Works

1. **Lock Liquidity**: Users deposit funds and open positions predicting price movement
2. **24-Hour Period**: Positions are locked for exactly 24 hours from creation
3. **Automatic Settlement**: After 24 hours, positions can be settled with the exit price
4. **Reward Calculation**: 
   - Winners get their original amount back
   - Winners share 95% of the losing pool proportionally
   - 5% remains as protocol fee
5. **Distribution**: Rewards are automatically credited to user balances

## Example Scenario

```python
# User A predicts BTC will go UP
market.execute(
    action='deposit',
    user_address='0xAAA',
    amount=1000.0
)

market.execute(
    action='open_position',
    user_address='0xAAA',
    amount=100.0,
    direction='up',
    current_price=50000.0,
    asset='BTC'
)

# User B predicts BTC will go DOWN
market.execute(
    action='deposit',
    user_address='0xBBB',
    amount=1000.0
)

market.execute(
    action='open_position',
    user_address='0xBBB',
    amount=100.0,
    direction='down',
    current_price=50000.0,
    asset='BTC'
)

# 24 hours later, BTC is at 51000 (went UP)
# User A wins, User B loses

# Settle positions
market.execute(
    action='settle_position',
    position_id=position_a_id,
    exit_price=51000.0
)

market.execute(
    action='settle_position',
    position_id=position_b_id,
    exit_price=51000.0
)

# Distribute rewards
market.execute(
    action='distribute_rewards',
    asset='BTC',
    settlement_time='2024-01-16T10:00:00'
)

# User A receives: 100 (original) + 95 (95% of User B's 100) = 195
# User B receives: 0
# Protocol keeps: 5 (5% fee)
```

## Configuration

- `reward_pool_percentage`: Percentage of losing pool distributed to winners (default: 0.95 = 95%)

## API Reference

### Actions

- `deposit`: Add funds to user balance
- `open_position`: Create a new 24-hour prediction
- `settle_position`: Settle a position after 24 hours
- `distribute_rewards`: Distribute rewards to profitable traders
- `get_balance`: Check user's available balance
- `get_positions`: View active positions

## Security Considerations

- Positions can only be settled after 24 hours
- Users can only spend their available balance
- All calculations are deterministic and transparent
- Position IDs are cryptographically generated

## License

MIT License
