# Frontend App

The frontend is a Next.js 14 application with TypeScript, Tailwind CSS, and ethers.js v6. It provides a web interface for interacting with the BlocTime Protocol, managing modules, AI chat, and more.

## Tech Stack

- **Next.js** 14.0.4 (App Router)
- **TypeScript**
- **Tailwind CSS**
- **ethers.js** v6 (blockchain interaction)
- **react-toastify** (notifications)
- **Target**: Base Sepolia testnet (chainId `84532`)

## Project Structure

```
mod/core/app/src/
├── app/                    # Next.js App Router pages
│   ├── layout.tsx          # Root layout (providers, sidebar, top bar)
│   ├── providers.tsx       # Context provider hierarchy
│   ├── home/               # Landing page
│   ├── chat/               # AI chat interface
│   ├── market/             # Market credit/debit
│   ├── treasury/           # Treasury management
│   ├── safe/               # Safe multisig wallet
│   ├── bridge/             # Token bridge
│   ├── contracts/          # Contract interaction
│   ├── quests/             # Quest system
│   ├── traders/            # Trader analytics
│   ├── transactions/       # Transaction history
│   ├── buidl/              # Builder tools
│   ├── create/             # Module creation
│   ├── mod/                # Module browser
│   ├── cid/                # IPFS CID viewer
│   ├── api/                # API explorer
│   ├── network/            # Network info
│   ├── docs/               # In-app docs
│   ├── jobs/               # Background jobs
│   └── user/               # User profile
│
├── network/                # Blockchain interaction layer
│   ├── Market.ts           # Market contract wrapper
│   ├── Treasury.ts         # Treasury contract wrapper
│   ├── safe.ts             # Safe multisig wrapper
│   ├── network.ts          # Network utilities
│   └── NetworkSelector.tsx # Network switcher component
│
├── context/                # React contexts
├── wallet/                 # MetaMask integration
├── components/             # Shared UI components
└── config/                 # App configuration
```

## Configuration

Contract addresses are stored in `src/app/mod.json` (or `src/config.json`) under `chain.testnet.contracts`:

```json
{
  "chain": {
    "testnet": {
      "contracts": {
        "USDC": "0xe22970F0bB899C7D615ED522B2A807629F99ec01",
        "Market": "0x2F0B61616Fbf662A4f4C544D7d5d909D74ef7687",
        "Treasury": "0xe9a96Ae58108E9Dd7e14c5DdCb66C175BB877785",
        "Registry": "0x4f9e72C935e5762E941F98DA50696cb022008a43"
      }
    }
  }
}
```

## Context Provider Hierarchy

The app wraps everything in nested providers (defined in `providers.tsx`):

```
ThemeProvider
  → MetaMaskProvider
    → UserProvider
      → MarketCreditProvider
        → SearchProvider
          → SplitScreenProvider
            → ControlPanelProvider
              → LayoutProvider
```

Key contexts:
- **UserProvider**: Wallet connection state, `user.key` = wallet address
- **MarketCreditProvider**: Market token balances
- **ThemeProvider**: Dark/light mode
- **SplitScreenProvider**: Vertical/horizontal split with iframe panel

## Network Layer

The `src/network/` directory contains TypeScript wrappers for contract interaction.

### Market.ts

```typescript
import { Market } from '@/network/Market'

// Check balances
const usdcBalance = await Market.checkBalance(userAddress, 'USDC')
const marketBalance = await Market.checkMarketBalance(userAddress)

// Approve tokens
await Market.increaseMarketAllowance(userAddress, amount, 'USDC')

// Deposit (credit)
await Market.addMarketCredit(userAddress, amount, 'USDC')

// Withdraw
await Market.withdrawMarketCredit(userAddress, amount, 'USDC')

// Transfer Market tokens
await Market.transferMarketCredit(fromAddress, toAddress, amount)
```

### Provider Pattern

```typescript
// All network functions use this pattern:
function getEthereumProvider() {
  if (!window.ethereum) throw new Error('No wallet detected')
  return new ethers.BrowserProvider(window.ethereum)
}

// Then get signer for transactions:
const provider = getEthereumProvider()
const signer = await provider.getSigner(userAddress)
const contract = new ethers.Contract(address, abi, signer)
```

## Layout

- **Top Bar**: 64px height, fixed at top — wallet connection, theme toggle
- **Sidebar**: 220px width, fixed at left — navigation tabs
- **Main Content**: Fills remaining space
- **Split Screen**: Optional second panel (iframe) for side-by-side views

Navigation items are defined in the `navItems` array in `providers.tsx`.

## Fonts

| Font | Usage |
|------|-------|
| Inter | Body text |
| VT323 | Digital/terminal style |
| Orbitron | Headers/branding |
| Press Start 2P | Pixel art style |

## Running the Frontend

```bash
cd mod/core/app
npm install
npm run dev
```

Runs at `http://localhost:3000` by default.

## Important Gotchas

**BigInt Literals**: The TypeScript target is below ES2020, so you cannot use `0n` syntax. Always use `BigInt(0)` instead.

```typescript
// WRONG — will fail to compile
const zero = 0n

// CORRECT
const zero = BigInt(0)
```

**window.ethereum**: Must null-check before creating a provider, especially for SSR:

```typescript
// WRONG
const provider = new ethers.BrowserProvider(window.ethereum)

// CORRECT
if (!window.ethereum) throw new Error('Install MetaMask')
const provider = new ethers.BrowserProvider(window.ethereum)
```

**Safe SDK**: The `@safe-global/protocol-kit` and `api-kit` packages have Node.js dependencies that break Next.js webpack bundling, even with dynamic imports. Use direct ethers.js calls to Safe contract ABIs instead.
