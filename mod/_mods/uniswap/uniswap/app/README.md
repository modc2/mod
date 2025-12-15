# Uniswap Base Swap Frontend

A Next.js frontend for swapping tokens on Uniswap V3 on Base network.

## Features

- 🔄 Token swaps on Base using Uniswap V3
- 💼 Wallet connection with RainbowKit
- 🎨 Beautiful UI with Tailwind CSS
- ⚡ Fast and responsive
- 🔒 Slippage protection

## Setup

1. Install dependencies:
```bash
npm install
```

2. Get a WalletConnect Project ID:
   - Go to https://cloud.walletconnect.com/
   - Create a new project
   - Copy the Project ID
   - Replace `YOUR_PROJECT_ID` in `app/providers.tsx`

3. Run the development server:
```bash
npm run dev
```

4. Open [http://localhost:3000](http://localhost:3000)

## Usage

1. Connect your wallet (make sure you're on Base network)
2. Select tokens to swap (WETH/USDC)
3. Enter amount
4. Set slippage tolerance
5. Click "Swap" and confirm transaction

## Supported Tokens

- WETH: 0x4200000000000000000000000000000000000006
- USDC: 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913

## Tech Stack

- Next.js 14
- React 18
- TypeScript
- Tailwind CSS
- Wagmi
- RainbowKit
- Ethers.js
- Uniswap V3 SDK

## Notes

- Make sure you have Base ETH for gas fees
- Approve token spending before swapping
- Check slippage settings for volatile pairs
- Always verify transaction details before confirming
