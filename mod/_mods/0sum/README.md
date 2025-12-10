# 0SUM - Substrate Decay Protocol

## Overview
A substrate-based protocol implementing forced token transfers with decay mechanics and inflation redistribution.

## Features

### 🔄 Forced Transfers
- Wallets are compelled to transfer tokens to other participants
- Transfers are executed automatically by the protocol
- Creates constant token velocity

### ⏰ Decay Function
- Inactive wallets (stale keys) face decay penalties
- 24-hour activity threshold
- 1% decay rate per period for inactive accounts
- Decayed tokens flow into inflation pool

### 💰 Inflation Redistribution
- Inflation pool funded by decayed tokens
- Random distribution to early registered keys
- 30% chance per protocol cycle
- Rewards early adopters and active participants

### 🏆 Scoring System
- Score based on total amount transferred
- Leaderboard tracks most active wallets
- Higher transfer volume = higher score
- Incentivizes participation

## Architecture

```
DecayProtocol
├── Wallet Registration
├── Decay Calculator
├── Force Transfer Engine
├── Inflation Pool Manager
└── Scoring System
```

## Usage

```python
from substrate_protocol import DecayProtocol

# Initialize protocol
protocol = DecayProtocol(substrate_url='ws://127.0.0.1:9944')

# Register wallets
protocol.register_wallet(address, keypair)

# Run protocol cycle
protocol.run_protocol_cycle()

# Check leaderboard
leaderboard = protocol.get_leaderboard()
```

## Mechanics

1. **Registration**: Wallets register with the protocol
2. **Activity Tracking**: Last activity timestamp recorded per wallet
3. **Decay Check**: Inactive wallets identified and penalized
4. **Forced Transfers**: Random wallet pairs execute transfers
5. **Inflation Distribution**: Random early keys receive rewards
6. **Score Update**: Transfer amounts added to wallet scores

## Parameters

- `decay_threshold`: 86400 seconds (24 hours)
- `decay_rate`: 0.01 (1% per period)
- `transfer_amount`: 10% of wallet balance
- `distribution_chance`: 30% per cycle

## Game Theory

This protocol creates a zero-sum game where:
- Activity is rewarded (avoid decay)
- Early participation is incentivized (inflation rewards)
- Transfer volume determines ranking
- Inactivity is punished (decay penalty)

## License
MIT
