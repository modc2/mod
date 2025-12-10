# Hyperliquid Module

A comprehensive Python module for interacting with the Hyperliquid DEX API.

## Features

- **Market Data Fetching**: Get real-time market data, orderbooks, and candlestick charts
- **Trading**: Place limit orders, market orders, and cancel orders
- **Account Management**: Fetch user positions, balances, and trade history
- **Testnet Support**: Test your strategies on testnet before going live

## Installation

```bash
pip install requests
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
```

### Account Information

```python
# Get user state (positions, balances)
user_state = hl.fetch_user_state("0xYourAddress")

# Get open orders
open_orders = hl.get_open_orders("0xYourAddress")

# Get trade history
fills = hl.get_user_fills("0xYourAddress")
```

## API Methods

### Public Methods (No Authentication Required)
- `fetch_market_data(symbol)` - Get market metadata and asset contexts
- `fetch_orderbook(symbol)` - Get L2 orderbook data
- `fetch_candles(symbol, interval, start_time, end_time)` - Get OHLCV candlestick data
- `fetch_user_state(address)` - Get user positions and balances
- `get_open_orders(address)` - Get open orders for an address
- `get_user_fills(address)` - Get trade fills for an address

### Authenticated Methods (Requires API Key)
- `place_order(symbol, is_buy, size, price, order_type, reduce_only)` - Place a limit order
- `market_order(symbol, is_buy, size)` - Place a market order
- `cancel_order(symbol, order_id)` - Cancel an existing order

## Notes

- Always test on testnet first before trading with real funds
- Keep your API keys secure and never commit them to version control
- Rate limits apply - be mindful of request frequency
- All prices and sizes should be in the appropriate decimal format

## License

MIT
