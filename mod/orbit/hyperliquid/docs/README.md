# Hyperliquid Trading Module

A comprehensive trading platform for Hyperliquid DEX (perpetual futures) with vault management and trader exploration features, including a full-featured web dashboard.

## 🚀 Quick Start - Web Dashboard

Get started with the full trading dashboard in seconds:

```bash
# Start the dashboard (testnet mode)
./start.sh testnet

# Access at http://localhost:3002
```

See [README_DASHBOARD.md](README_DASHBOARD.md) for complete dashboard documentation.

## Features

### Python Module
- **Market Data Fetching**: Get real-time market data, orderbooks, and candlestick charts
- **Trading**: Place limit orders, market orders, and cancel orders
- **Account Management**: Fetch user positions, balances, and trade history
- **Vault Access**: Deposit into vaults, track performance, explore top vaults
- **Trader Exploration**: Leaderboards, trader profiles, comprehensive analytics
- **Testnet Support**: Test your strategies on testnet before going live

### Web Dashboard
- **Interactive Trading Interface**: Place orders with a beautiful UI
- **Real-time Charts**: Live price charts with multiple timeframes
- **Position Management**: Track and manage all your positions
- **Vault Discovery**: Browse and invest in top-performing vaults
- **Trader Analytics**: Explore leaderboards and analyze top traders
- **Agent-Ready API**: REST API for automated trading strategies

## Installation

```bash
# Install Python dependencies
pip install requests

# No additional dependencies required
```

## Usage

### Initialize the Module

```python
from hyperliquid.mod import HyperliquidMod

# For public data (no authentication needed)
hl = HyperliquidMod()

# For trading (requires API credentials)
hl = HyperliquidMod(api_key="your_api_key", api_secret="your_api_secret")

# For testnet
hl = HyperliquidMod(api_key="your_api_key", api_secret="your_api_secret", testnet=True)
```

### Fetch Market Data

```python
# Get market data
market_data = hl.fetch_market_data("BTC")

# Get orderbook
orderbook = hl.fetch_orderbook("ETH")

# Get candlestick data
candles = hl.fetch_candles("BTC", interval="1h")

# Get all mid prices
mids = hl.fetch_all_mids()
```

### Trading

```python
# Place a limit buy order
order = hl.place_order(
    symbol="BTC",
    is_buy=True,
    size=0.1,
    price=50000
)

# Place a market order
market_order = hl.market_order(
    symbol="ETH",
    is_buy=False,
    size=1.0
)

# Cancel an order
hl.cancel_order(symbol="BTC", order_id=12345)

# Close a position
hl.close_position(symbol="BTC", address="0xYourAddress")
```

### Vault Management

```python
# List all available vaults
vaults = hl.list_vaults()

# Get top performing vaults
top_vaults = hl.get_top_vaults(sort_by="pnl", limit=10)

# Get vault details
vault_details = hl.get_vault_details("0xVaultAddress")

# Get vault performance history
performance = hl.get_vault_performance("0xVaultAddress")

# Check your balance in a vault
balance = hl.get_user_vault_balance("0xYourAddress", "0xVaultAddress")

# Deposit USDC into a vault (requires API key)
deposit = hl.deposit_to_vault("0xVaultAddress", 100.0)

# Withdraw from a vault (requires API key)
withdraw = hl.withdraw_from_vault("0xVaultAddress", 50.0)

# Get comprehensive vault analysis
analysis = hl.analyze_vault("0xVaultAddress")

# Get your vault transaction history
history = hl.get_user_vault_history("0xYourAddress")
```

### Trader Exploration

```python
# Get PnL leaderboard
pnl_leaders = hl.get_leaderboard("pnl")

# Get ROI leaderboard
roi_leaders = hl.get_leaderboard("roi")

# Get volume leaderboard
volume_leaders = hl.get_leaderboard("volume")

# Get trader profile
profile = hl.get_user_profile("0xTraderAddress")

# Get trader statistics
stats = hl.get_user_trade_stats("0xTraderAddress")

# Get trader PnL history
pnl_history = hl.get_user_pnl_history("0xTraderAddress")

# Search for high-volume traders
high_volume = hl.search_traders_by_volume(min_volume=1000000)

# Comprehensive trader analysis
analysis = hl.analyze_trader("0xTraderAddress")
```

### Account Information

```python
# Get user state (positions, balances)
user_state = hl.fetch_user_state("0xYourAddress")

# Get open orders
open_orders = hl.get_open_orders("0xYourAddress")

# Get trade history
fills = hl.get_user_fills("0xYourAddress")

# Get specific position
position = hl.get_position("0xYourAddress", "BTC")

# Get balance info
balance = hl.get_balance("0xYourAddress")
```

## API Methods

### Public Methods (No Authentication Required)

**Market Data:**
- `fetch_market_data(symbol)` - Get market metadata and asset contexts
- `fetch_orderbook(symbol)` - Get L2 orderbook data
- `fetch_candles(symbol, interval, start_time, end_time)` - Get OHLCV candlestick data
- `fetch_all_mids()` - Get mid prices for all assets
- `fetch_user_funding(address, start_time, end_time)` - Get funding history

**Account Data:**
- `fetch_user_state(address)` - Get user positions and balances
- `get_open_orders(address)` - Get open orders for an address
- `get_user_fills(address)` - Get trade fills for an address
- `get_position(address, symbol)` - Get specific position
- `get_balance(address)` - Get account balance info
- `get_user_rate_limit(address)` - Get rate limit info

**Vaults:**
- `list_vaults()` - Get all available vaults
- `get_vault_details(vault_address)` - Get vault details
- `get_vault_performance(vault_address, start_time, end_time)` - Get performance history
- `get_user_vault_balance(address, vault_address)` - Get user vault balance
- `get_user_vault_history(address)` - Get vault transaction history
- `get_vault_leaderboard()` - Get vault leaderboard
- `get_top_vaults(sort_by, limit)` - Get top performing vaults
- `analyze_vault(vault_address)` - Comprehensive vault analysis

**Traders:**
- `get_leaderboard(leaderboard_type)` - Get trader leaderboard (pnl/roi/volume)
- `get_user_profile(address)` - Get trader profile
- `get_user_pnl_history(address, start_time, end_time)` - Get PnL history
- `get_user_trade_stats(address)` - Get trading statistics
- `search_traders_by_volume(min_volume)` - Find high-volume traders
- `analyze_trader(address)` - Comprehensive trader analysis

### Authenticated Methods (Requires API Key)

**Trading:**
- `place_order(symbol, is_buy, size, price, order_type, reduce_only)` - Place a limit order
- `market_order(symbol, is_buy, size)` - Place a market order
- `cancel_order(symbol, order_id)` - Cancel an existing order
- `cancel_all_orders(symbol)` - Cancel all orders
- `modify_order(symbol, order_id, new_price, new_size)` - Modify an order
- `close_position(symbol, address)` - Close a position

**Vaults:**
- `deposit_to_vault(vault_address, amount)` - Deposit USDC to vault
- `withdraw_from_vault(vault_address, amount)` - Withdraw from vault

## REST API Endpoints

The web dashboard includes a FastAPI backend with the following endpoints:

### Market Data
- `GET /market/{symbol}` - Market data
- `GET /orderbook/{symbol}` - Orderbook
- `GET /candles/{symbol}?interval=1h` - Candlestick data

### Trading
- `POST /order` - Place order
- `DELETE /order/{symbol}/{order_id}` - Cancel order
- `POST /position/{symbol}/close` - Close position

### Account
- `GET /positions` - Get positions
- `GET /stats` - Account statistics
- `GET /user?address=0x...` - User state

### Vaults
- `GET /vaults` - List all vaults
- `GET /vaults/top?sort_by=pnl&limit=10` - Top vaults
- `GET /vaults/leaderboard` - Vault leaderboard
- `GET /vault/{vault_address}` - Vault details
- `GET /vault/{vault_address}/performance` - Vault performance
- `GET /vault/{vault_address}/analyze` - Vault analysis
- `POST /vault/deposit` - Deposit to vault
- `POST /vault/withdraw` - Withdraw from vault

### Traders
- `GET /leaderboard?leaderboard_type=pnl` - Trader leaderboard
- `GET /trader/{address}` - Trader profile
- `GET /trader/{address}/stats` - Trader statistics
- `GET /trader/{address}/pnl` - Trader PnL history
- `GET /trader/{address}/analyze` - Comprehensive trader analysis
- `GET /traders/top?days=7&pool=all&rank=roi&enrich=120&sort=sharpe&min_sharpe=1` - The trader board. `pool=all` = every gated wallet (≥ $1k equity, traded ≤24h; ~5k) priced from the leaderboard; fill stats (win%/sharpe/trades/coins) are fetched for the top `enrich` rows by `rank` only (default 120, max 400). Floors: `min_roi min_pnl min_volume min_equity min_sharpe min_win min_trades with_stats` (a stat floor implies `with_stats`); `sort` = roi|pnl|volume|equity|sharpe|win_rate|trades. Counts: `candidates` (universe), `priced`, `matched`, `enriched`. `coins=ZEC,ENA` is a requirement — the scan walks the leaderboard until `pool` wallets that traded one of them are found; `depth` says how far

## Example Use Cases

### Copy Trading
```python
# Find top performing traders
top_traders = hl.get_leaderboard("roi")[:5]

# Analyze their strategies
for trader in top_traders:
    analysis = hl.analyze_trader(trader['address'])
    positions = analysis['state']['assetPositions']
    # Mirror their positions
```

### Vault Investment
```python
# Find best performing vaults
top_vaults = hl.get_top_vaults(sort_by="apy", limit=5)

# Analyze each vault
for vault in top_vaults:
    analysis = hl.analyze_vault(vault['address'])
    if analysis['details']['apy'] > 50:  # 50% APY
        # Deposit into vault
        hl.deposit_to_vault(vault['address'], 1000)
```

### Automated Trading
```python
# Monitor market and execute trades
while True:
    # Check leaderboard for signals
    leaders = hl.get_leaderboard("volume")

    # Analyze top trader positions
    top_trader = leaders[0]
    analysis = hl.analyze_trader(top_trader['address'])

    # Execute trades based on analysis
    for position in analysis['state']['assetPositions']:
        symbol = position['position']['coin']
        # Your trading logic here
```

## Notes

- Always test on testnet first before trading with real funds
- Keep your API keys and private keys secure and never commit them to version control
- Rate limits apply - be mindful of request frequency
- All prices and sizes should be in the appropriate decimal format
- Vault deposits/withdrawals are in USDC

## License

MIT
