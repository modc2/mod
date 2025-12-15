# Uniswap V3 MCP Server

A production-ready Model Context Protocol (MCP) server for Uniswap V3 on Base Network with MEV protection, gas optimization, and comprehensive swap functionality.

## 🚀 Features
- ✅ Real-time swap quotes with accurate pricing
- ✅ Execute swaps with MEV protection
- ✅ Token balance queries for any ERC20
- ✅ Gas optimization and estimation
- ✅ Slippage protection (configurable)
- ✅ Base network integration (Mainnet)
- ✅ Docker support for easy deployment
- ✅ Automatic token approval handling

## 📋 Prerequisites

- Python 3.11+
- Docker & Docker Compose (optional)
- Base Network RPC URL (free from base.org)
- Wallet private key with ETH on Base for gas fees

## 🔧 Installation

### Method 1: Local Python Setup

1. **Clone and navigate to the server directory:**
```bash
cd /root/mod/mod/_mods/uniswap/uniswap/server
```

2. **Install Python dependencies:**
```bash
pip install -r requirements.txt
```

3. **Configure environment variables:**
```bash
cp .env.example .env
nano .env  # Edit with your credentials
```

4. **Add your credentials to `.env`:**
```env
BASE_RPC_URL=https://mainnet.base.org
PRIVATE_KEY=your_private_key_without_0x_prefix
```

5. **Run the server:**
```bash
python server.py
```

### Method 2: Docker Deployment (Recommended)

1. **Setup environment:**
```bash
cd /root/mod/mod/_mods/uniswap/uniswap/server
cp .env.example .env
nano .env  # Add your credentials
```

2. **Build and run with Docker Compose:**
```bash
docker-compose up -d
```

3. **View logs:**
```bash
docker-compose logs -f
```

4. **Stop the server:**
```bash
docker-compose down
```

### Method 3: Docker Manual Build

```bash
docker build -t uniswap-mcp-server .
docker run -d \
  --name uniswap-mcp \
  -p 8000:8000 \
  -e BASE_RPC_URL=https://mainnet.base.org \
  -e PRIVATE_KEY=your_private_key \
  uniswap-mcp-server
```

## 🔑 Getting Your Private Key

**⚠️ SECURITY WARNING: Never share your private key or commit it to git!**

### From MetaMask:
1. Open MetaMask
2. Click the three dots → Account Details
3. Click "Export Private Key"
4. Enter your password
5. Copy the key (without 0x prefix)

### From Other Wallets:
- **Coinbase Wallet**: Settings → Security → Show Private Key
- **Trust Wallet**: Settings → Wallets → Info → Export Private Key

## 📡 API Methods

### 1. Get Quote
Get a swap quote without executing the transaction.

```json
{
  "method": "get_quote",
  "params": {
    "token_in": "0x4200000000000000000000000000000000000006",
    "token_out": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    "amount_in": 0.01
  }
}
```

**Response:**
```json
{
  "success": true,
  "token_in": "0x4200000000000000000000000000000000000006",
  "token_out": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
  "amount_in": 0.01,
  "amount_in_wei": "10000000000000000",
  "estimated_gas": "150000"
}
```

### 2. Execute Swap
Execute a token swap with slippage protection.

```json
{
  "method": "execute_swap",
  "params": {
    "token_in": "0x4200000000000000000000000000000000000006",
    "token_out": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    "amount_in": 0.01,
    "slippage": 0.5
  }
}
```

**Response:**
```json
{
  "success": true,
  "tx_hash": "0xabc123...",
  "block_number": 12345678,
  "gas_used": 180000
}
```

### 3. Get Balance
Query token balance for any wallet.

```json
{
  "method": "get_balance",
  "params": {
    "token_address": "0x4200000000000000000000000000000000000006",
    "wallet_address": "0xYourWalletAddress"
  }
}
```

**Response:**
```json
{
  "success": true,
  "balance_wei": "1500000000000000000",
  "balance": 1.5,
  "decimals": 18
}
```

## 🪙 Common Token Addresses on Base

| Token | Address |
|-------|----------|
| WETH | `0x4200000000000000000000000000000000000006` |
| USDC | `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` |
| DAI | `0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb` |
| USDT | `0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2` |

## 🛠️ Troubleshooting

### "No private key configured" Error
- Ensure `.env` file exists with valid `PRIVATE_KEY`
- Remove `0x` prefix from private key
- Restart the server after updating `.env`

### "Insufficient funds" Error
- Ensure wallet has enough ETH for gas on Base Network
- Ensure wallet has enough of the input token
- Check token balance using `get_balance` method

### Connection Issues
- Verify `BASE_RPC_URL` is accessible
- Try alternative RPC: `https://base.llamarpc.com`
- Check firewall settings for port 8000

### Docker Issues
```bash
# Rebuild container
docker-compose down
docker-compose build --no-cache
docker-compose up -d

# Check logs
docker-compose logs -f
```

## 🔒 Security Best Practices

1. **Never commit `.env` file to git**
2. **Use a dedicated wallet for trading** (not your main wallet)
3. **Start with small amounts** to test
4. **Keep private keys encrypted** at rest
5. **Use environment variables** in production
6. **Monitor gas prices** before large swaps
7. **Set appropriate slippage** (0.5% - 2% typical)

## 📊 Gas Optimization

- Approval transactions: ~50,000 gas
- Swap transactions: ~150,000-200,000 gas
- Server automatically handles approvals
- Infinite approval used to save gas on future swaps

## 🧪 Testing

```bash
# Test with small amount first
python server.py

# Expected output:
# 🚀 Uniswap MCP Server Running on Base
# Router: 0x2626664c2603336E57B271c5C0b26F421741e481
# Connected: True
# Quote: {...}
```

## 📝 Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `BASE_RPC_URL` | Yes | Base Network RPC endpoint |
| `PRIVATE_KEY` | Yes | Wallet private key (no 0x) |

## 🤝 Support

For issues or questions:
1. Check troubleshooting section
2. Verify all prerequisites are met
3. Test with small amounts first
4. Review Docker logs if using containers

## ⚖️ License

MIT License - Use at your own risk. This is experimental software.

## ⚠️ Disclaimer

**This software is provided as-is. Trading cryptocurrencies involves risk. Always:**
- Test with small amounts
- Understand slippage and MEV
- Keep private keys secure
- Monitor gas costs
- DYOR (Do Your Own Research)

---

**Built with ❤️ for the Base ecosystem**
