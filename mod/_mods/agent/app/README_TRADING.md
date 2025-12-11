# Trading Rewards Platform

## Overview
A decentralized trading rewards system that mints tokens for profitable trades and burns tokens for losing trades across multiple chains.

## Features
- ✅ Multi-chain support (Ethereum, Base, Polygon, Arbitrum, Optimism, Solana)
- ✅ Uniswap V2 integration for EVM chains
- ✅ Raydium integration for Solana
- ✅ Automatic reward minting for profitable trades
- ✅ Token burning mechanism for losses
- ✅ Trader scoring system
- ✅ Simple React frontend

## Smart Contracts

### EVM (Solidity)
**Location:** `/contracts/TradingRewards.sol`

**Key Functions:**
- `openTrade()` - Opens a new trade position
- `closeTrade()` - Closes trade and distributes rewards/burns tokens
- `getTradeInfo()` - Get trade details
- `getTraderScore()` - Get trader's cumulative score

### Solana (Rust/Anchor)
**Location:** `/contracts/solana/trading_rewards.rs`

**Key Instructions:**
- `initialize` - Initialize the program
- `open_trade` - Open a new trade
- `close_trade` - Close trade with reward/burn logic

## Deployment

### EVM Chains

1. Install dependencies:
```bash
cd contracts
npm install
```

2. Compile contracts:
```bash
npm run compile
```

3. Deploy to Ganache (local):
```bash
npm run deploy:ganache
```

4. Deploy to Base:
```bash
export PRIVATE_KEY=your_private_key
npm run deploy:base
```

5. Deploy to Base Sepolia (testnet):
```bash
npm run deploy:baseSepolia
```

### Solana

1. Build the program:
```bash
cd contracts/solana
cargo build-bpf
```

2. Deploy:
```bash
solana program deploy target/deploy/trading_rewards.so
```

## Frontend Usage

### Setup
1. The frontend component is located at `/app/trading/TradingInterface.tsx`
2. Import it in your main page:

```tsx
import TradingInterface from './trading/TradingInterface';

function Page() {
  return <TradingInterface />;
}
```

### Features
- Connect Web3 wallet (MetaMask, etc.)
- Open trades with any token pair
- Close trades and receive rewards
- View trader score and token balance
- Configurable slippage protection

## How It Works

1. **Opening a Trade:**
   - User specifies token pair and amount
   - Contract swaps tokens via Uniswap/Raydium
   - Trade details stored on-chain

2. **Closing a Trade:**
   - User closes position
   - Contract swaps back to original token
   - Calculates profit/loss
   - **Profitable:** Mints reward tokens based on profit %
   - **Loss:** Burns tokens from user's balance

3. **Reward Calculation:**
   - Reward = (Profit / Initial Amount) × 1000 tokens
   - Burn = (Loss / Initial Amount) × 1000 tokens

## Network Support

### EVM Chains
- **Ganache** (Local testing)
- **Base** (Mainnet)
- **Base Sepolia** (Testnet)
- **Polygon**
- **Arbitrum**
- **Optimism**

### Solana
- **Mainnet Beta**
- **Devnet**
- **Testnet**

## Security Considerations

⚠️ **Important:**
- This is a simplified implementation for demonstration
- Audit contracts before mainnet deployment
- Add access controls and emergency pause mechanisms
- Implement proper oracle price feeds
- Add MEV protection
- Test thoroughly on testnets

## Configuration

### Environment Variables
```bash
PRIVATE_KEY=your_wallet_private_key
CONTRACT_ADDRESS=deployed_contract_address
```

### Uniswap Router Addresses
- Base: `0x4752ba5dbc23f44d87826276bf6fd6b1c372ad24`
- Polygon: `0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff`
- Arbitrum: `0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506`
- Optimism: `0x4A7b5Da61326A6379179b40d00F57E5bbDC962c2`

## Testing

```bash
cd contracts
npm test
```

## License
MIT

## Support
For issues and questions, please open a GitHub issue.

---
**Built with ❤️ for DeFi traders**