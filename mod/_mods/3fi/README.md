# 3Fi Prediction Market

A decentralized prediction market for forecasting future prices of trading pairs on Raydium (Solana) and Uniswap (Base).

## Features

- **Multi-chain Support**: Deploy on both Solana and Base networks
- **Price Predictions**: Bet on whether a trading pair will be above or below a target price
- **Automated Settlement**: Markets settle automatically at expiry
- **Proportional Payouts**: Winners share the total pool proportionally to their stake
- **Modern UI**: Next.js frontend with Tailwind CSS

## Smart Contracts

### Solana (Anchor)
- Location: `contracts/solana/prediction_market.rs`
- Features:
  - Initialize prediction markets
  - Place predictions (YES/NO)
  - Settle markets with oracle price
  - Claim winnings

### Base (Solidity)
- Location: `contracts/base/PredictionMarket.sol`
- Features:
  - ERC20 token staking
  - Market creation and management
  - Prediction placement
  - Automated settlement
  - Winnings distribution

## Frontend

### Tech Stack
- Next.js 14
- TypeScript
- Tailwind CSS
- Solana Wallet Adapter
- Wagmi (for Base/Ethereum)
- Web3Modal

### Setup

```bash
cd frontend
npm install
npm run dev
```

## Deployment

### Solana

```bash
cd contracts/solana
anchor build
anchor deploy
```

### Base

```bash
cd contracts/base
npx hardhat compile
npx hardhat run scripts/deploy.js --network base
```

## Usage

1. **Connect Wallet**: Choose Solana or Base chain and connect your wallet
2. **Browse Markets**: View active prediction markets
3. **Place Prediction**: Select a market, enter amount, choose YES or NO
4. **Wait for Settlement**: Markets settle at expiry timestamp
5. **Claim Winnings**: If you predicted correctly, claim your share of the pool

## Market Mechanics

- Users stake tokens to predict if price will be above (YES) or below (NO) target
- All stakes go into a shared pool
- At expiry, oracle provides final price
- Winners split the entire pool proportionally to their stake
- Example: If you staked 10% of winning side, you get 10% of total pool

## Security

- ReentrancyGuard on all state-changing functions
- Access control for market settlement
- Timestamp validation for market expiry
- Claim prevention for losers and already-claimed predictions

## License

MIT