# X402 Payment Gateway with Whitelist Support

Fully functional Docker-based HTTP middleware implementing the x402 Payment Required protocol with smart contract and off-chain whitelist support.

## Features

- **Payment Gating**: Protect endpoints with crypto payments (Solana, Ethereum, Base)
- **Whitelist Support**: 
  - On-chain: Read whitelisted wallets from a smart contract
  - Off-chain: Read from local JSON file or remote API
- **Multiple Networks**: Solana, Ethereum, Base, Polygon, Arbitrum
- **Multiple Currencies**: USDC, USDT, SOL, ETH, MATIC
- **Rate Limiting & Caching**
- **CORS Support**
- **Health & Metrics Endpoints**

## Quick Start

```bash
# Copy environment file
cp .env.example .env

# Edit configuration
nano .env

# Start the server
docker-compose up -d
```

## Endpoints

| Endpoint | Description |
|----------|-------------|
| `/health` | Health check |
| `/metrics` | Server metrics |
| `/whitelist` | List all whitelisted addresses |
| `/whitelist/check/{address}` | Check if address is whitelisted |
| `/api/premium/*` | Protected premium API |
| `/ipfs/premium/*` | Protected IPFS content |
| `/protected/*` | Generic protected resources |

## Whitelist Modes

### Off-chain (Default)

Edit `whitelist.json` to add/remove addresses:

```json
[
  "0x742d35Cc6634C0532925a3b844Bc9e7595f5bE21",
  "0x8ba1f109551bD432803012645Ac136ddd64DBA72"
]
```

### On-chain (Smart Contract)

1. Deploy `contracts/Whitelist.sol` to Base/Ethereum
2. Set environment variables:
   ```
   WHITELIST_MODE=onchain
   WHITELIST_CONTRACT=0xYourContractAddress
   WHITELIST_RPC=https://mainnet.base.org
   ```

## Payment Header Format

Clients must send payment proof in the `X-PAYMENT` header:

```json
{
  "wallet": "0xYourWalletAddress",
  "signature": "...",
  "transaction": "..."
}
```

Whitelisted wallets get free access automatically.

## Development

```bash
# Run with Ganache for testing
docker-compose --profile dev up -d

# Deploy whitelist contract to Ganache
python deploy_whitelist.py ganache
```

## License

MIT
