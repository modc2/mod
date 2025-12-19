# CoinGecko Module - Complete Tutorial 🎓

## 📋 Table of Contents

1. [Introduction](#introduction)
2. [What is the CoinGecko Module?](#what-is-the-coingecko-module)
3. [Installation & Setup](#installation--setup)
4. [Core Features](#core-features)
5. [Usage Examples](#usage-examples)
6. [Advanced Patterns](#advanced-patterns)
7. [API Reference](#api-reference)
8. [Best Practices](#best-practices)
9. [Troubleshooting](#troubleshooting)
10. [Extending the Module](#extending-the-module)

---

## Introduction

Welcome to the CoinGecko Module tutorial! This comprehensive guide will teach you everything you need to know about integrating cryptocurrency price data into your Python applications.

### What You'll Learn

✅ How to fetch real-time cryptocurrency prices
✅ Error handling and timeout management
✅ Building price monitoring systems
✅ Extending the module with custom functionality
✅ Production-ready implementation patterns

---

## What is the CoinGecko Module?

The CoinGecko Module is a lightweight, production-ready Python module that provides seamless integration with the CoinGecko cryptocurrency API. It's designed with three core principles:

1. **Simplicity** - Clean, intuitive API that just works
2. **Reliability** - Robust error handling and timeout protection
3. **Extensibility** - Easy to customize and extend

### Key Features

- 🚀 **Zero Configuration** - No API key required
- ⚡ **Fast & Efficient** - Optimized for performance
- 🛡️ **Error Resilient** - Graceful error handling
- 🔧 **Extensible** - Build on top with ease
- 📦 **Lightweight** - Minimal dependencies

---

## Installation & Setup

### Prerequisites

- Python 3.6 or higher
- `requests` library

### Step 1: Install Dependencies

```bash
pip install requests
```

### Step 2: Navigate to Module Directory

```bash
cd /root/mod/mod/_mods/coingecko
```

### Step 3: Verify Installation

```python
from coingecko.mod import BaseMod

mod = BaseMod()
print("Module loaded successfully!")
```

---

## Core Features

### 1. Cryptocurrency Price Fetching

Fetch real-time prices from CoinGecko's public API.

```python
from coingecko.mod import BaseMod

mod = BaseMod()
price = mod.get_bittenso_price()
print(f"Current Bittenso Price: {price}")
# Output: Current Bittenso Price: $0.00123
```

**How it works:**
- Makes HTTP GET request to CoinGecko API
- Parses JSON response
- Returns formatted USD price
- Handles errors gracefully

### 2. Mathematical Utilities

Built-in helper functions for calculations.

```python
mod = BaseMod()
result = mod.multiply(25, 4)
print(result)  # Output: 100
```

---

## Usage Examples

### Example 1: Simple Price Check

```python
from coingecko.mod import BaseMod

def check_price():
    """Check current cryptocurrency price."""
    mod = BaseMod()
    price = mod.get_bittenso_price()
    
    if "Error" in str(price):
        print(f"❌ Failed to fetch price: {price}")
    else:
        print(f"✅ Current Price: {price}")

check_price()
```

### Example 2: Price Monitoring System

```python
from coingecko.mod import BaseMod
import time
from datetime import datetime

def monitor_price(interval=60, duration=3600):
    """
    Monitor cryptocurrency price at regular intervals.
    
    Args:
        interval: Seconds between checks (default: 60)
        duration: Total monitoring time in seconds (default: 3600)
    """
    mod = BaseMod()
    start_time = time.time()
    
    print(f"🚀 Starting price monitor (interval: {interval}s)\n")
    
    while time.time() - start_time < duration:
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        price = mod.get_bittenso_price()
        
        print(f"[{timestamp}] Bittenso: {price}")
        time.sleep(interval)
    
    print("\n✅ Monitoring complete!")

# Monitor for 1 hour, checking every 60 seconds
monitor_price(interval=60, duration=3600)
```

### Example 3: Price Alert System

```python
from coingecko.mod import BaseMod
import time

def price_alert(target_price, check_interval=30):
    """
    Alert when price reaches target.
    
    Args:
        target_price: Price threshold to trigger alert
        check_interval: Seconds between price checks
    """
    mod = BaseMod()
    
    print(f"🎯 Watching for price >= ${target_price}\n")
    
    while True:
        price_str = mod.get_bittenso_price()
        
        if "Error" not in price_str:
            # Extract numeric price from "$X.XX" format
            price = float(price_str.replace('$', ''))
            print(f"Current: ${price:.6f}")
            
            if price >= target_price:
                print(f"\n🔔 ALERT! Price reached ${price:.6f}")
                break
        
        time.sleep(check_interval)

# Alert when price reaches $0.01
price_alert(0.01, check_interval=30)
```

### Example 4: Portfolio Value Calculator

```python
from coingecko.mod import BaseMod

def calculate_portfolio_value(holdings):
    """
    Calculate total portfolio value.
    
    Args:
        holdings: Dictionary of {coin: amount}
    """
    mod = BaseMod()
    total_value = 0
    
    print("📊 Portfolio Valuation\n")
    print("-" * 50)
    
    for coin, amount in holdings.items():
        if coin.lower() == 'bittenso':
            price_str = mod.get_bittenso_price()
            if "Error" not in price_str:
                price = float(price_str.replace('$', ''))
                value = mod.multiply(amount, int(price * 1000000)) / 1000000
                total_value += value
                print(f"{coin}: {amount} × ${price:.6f} = ${value:.2f}")
    
    print("-" * 50)
    print(f"Total Portfolio Value: ${total_value:.2f}")
    return total_value

# Example portfolio
my_portfolio = {
    'bittenso': 10000,
}

calculate_portfolio_value(my_portfolio)
```

### Example 5: Data Logger

```python
from coingecko.mod import BaseMod
import time
import json
from datetime import datetime

def log_prices(filename='price_log.json', interval=300, count=12):
    """
    Log cryptocurrency prices to JSON file.
    
    Args:
        filename: Output file path
        interval: Seconds between logs
        count: Number of data points to collect
    """
    mod = BaseMod()
    data = []
    
    print(f"📝 Logging {count} price points to {filename}\n")
    
    for i in range(count):
        timestamp = datetime.now().isoformat()
        price = mod.get_bittenso_price()
        
        entry = {
            'timestamp': timestamp,
            'price': price,
            'index': i + 1
        }
        
        data.append(entry)
        print(f"[{i+1}/{count}] Logged: {price} at {timestamp}")
        
        if i < count - 1:  # Don't sleep after last iteration
            time.sleep(interval)
    
    # Save to file
    with open(filename, 'w') as f:
        json.dump(data, f, indent=2)
    
    print(f"\n✅ Data saved to {filename}")

# Log 12 data points, 5 minutes apart (1 hour total)
log_prices('bittenso_prices.json', interval=300, count=12)
```

---

## Advanced Patterns

### Pattern 1: Retry Logic

```python
from coingecko.mod import BaseMod
import time

def fetch_price_with_retry(max_retries=3, delay=5):
    """
    Fetch price with automatic retry on failure.
    """
    mod = BaseMod()
    
    for attempt in range(max_retries):
        price = mod.get_bittenso_price()
        
        if "Error" not in str(price):
            return price
        
        if attempt < max_retries - 1:
            print(f"Retry {attempt + 1}/{max_retries} in {delay}s...")
            time.sleep(delay)
    
    return "Failed after all retries"
```

### Pattern 2: Caching

```python
from coingecko.mod import BaseMod
import time

class CachedPriceFetcher:
    def __init__(self, cache_duration=60):
        self.mod = BaseMod()
        self.cache = {}
        self.cache_duration = cache_duration
    
    def get_price(self, coin='bittenso'):
        """Get price with caching."""
        now = time.time()
        
        if coin in self.cache:
            cached_price, cached_time = self.cache[coin]
            if now - cached_time < self.cache_duration:
                return cached_price
        
        # Fetch fresh price
        price = self.mod.get_bittenso_price()
        self.cache[coin] = (price, now)
        return price

# Usage
fetcher = CachedPriceFetcher(cache_duration=60)
print(fetcher.get_price())  # Fetches from API
print(fetcher.get_price())  # Returns cached value
```

---

## API Reference

### Class: `BaseMod`

Core module class providing cryptocurrency data and utilities.

#### Methods

##### `get_bittenso_price() -> str`

Fetch current Bittenso cryptocurrency price from CoinGecko API.

**Parameters:** None

**Returns:**
- `str`: Formatted USD price ("$X.XX") or error message

**Raises:** None (errors returned as strings)

**Example:**
```python
mod = BaseMod()
price = mod.get_bittenso_price()
print(price)  # "$0.00123"
```

**Error Handling:**
- Network errors: Returns "Error fetching price: [details]"
- Timeout (10s): Returns timeout error message
- Invalid response: Returns parsing error message

##### `multiply(a: int, b: int) -> int`

Multiply two numbers.

**Parameters:**
- `a` (int): First number
- `b` (int): Second number

**Returns:**
- `int`: Product of a and b

**Example:**
```python
mod = BaseMod()
result = mod.multiply(10, 5)
print(result)  # 50
```

---

## Best Practices

### 1. Error Handling

Always check for errors in API responses:

```python
price = mod.get_bittenso_price()
if "Error" in str(price):
    # Handle error
    log_error(price)
else:
    # Use price
    process_price(price)
```

### 2. Rate Limiting

Respect API rate limits:

```python
import time

# Wait between requests
for i in range(10):
    price = mod.get_bittenso_price()
    print(price)
    time.sleep(2)  # 2 second delay
```

### 3. Timeout Configuration

The module uses 10-second timeouts by default. This prevents hanging requests.

### 4. Production Logging

```python
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

price = mod.get_bittenso_price()
logger.info(f"Fetched price: {price}")
```

### 5. Environment Variables

```python
import os

INTERVAL = int(os.getenv('PRICE_CHECK_INTERVAL', 60))
monitor_price(interval=INTERVAL)
```

---

## Troubleshooting

### Issue: "Error fetching price"

**Possible Causes:**
- No internet connection
- CoinGecko API is down
- Firewall blocking requests
- Invalid cryptocurrency ID

**Solutions:**
1. Check internet connection
2. Verify CoinGecko status: https://status.coingecko.com
3. Check firewall settings
4. Verify cryptocurrency ID is correct

### Issue: Import Errors

**Error:** `ModuleNotFoundError: No module named 'requests'`

**Solution:**
```bash
pip install requests
```

### Issue: Timeout Errors

**Error:** "Error fetching price: timeout"

**Solution:**
- Check network speed
- Increase timeout (requires modifying source)
- Implement retry logic

### Issue: Slow Performance

**Solutions:**
- Implement caching
- Reduce request frequency
- Use async requests (advanced)

---

## Extending the Module

### Add Support for Multiple Cryptocurrencies

```python
from coingecko.mod import BaseMod

class MultiCryptoMod(BaseMod):
    """Extended module supporting multiple cryptocurrencies."""
    
    def get_crypto_price(self, crypto_id, currency='usd'):
        """Fetch any cryptocurrency price."""
        import requests
        try:
            url = f'https://api.coingecko.com/api/v3/simple/price'
            params = {
                'ids': crypto_id,
                'vs_currencies': currency
            }
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            price = data.get(crypto_id, {}).get(currency, 'N/A')
            return f"${price}" if isinstance(price, (int, float)) else price
        except Exception as e:
            return f"Error: {str(e)}"
    
    def get_multiple_prices(self, crypto_ids):
        """Fetch multiple cryptocurrency prices at once."""
        import requests
        try:
            url = 'https://api.coingecko.com/api/v3/simple/price'
            params = {
                'ids': ','.join(crypto_ids),
                'vs_currencies': 'usd'
            }
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            return {"error": str(e)}

# Usage
mod = MultiCryptoMod()
print(mod.get_crypto_price('bitcoin'))  # Bitcoin price
print(mod.get_crypto_price('ethereum'))  # Ethereum price

prices = mod.get_multiple_prices(['bitcoin', 'ethereum', 'cardano'])
print(prices)
```

### Add Historical Data Support

```python
class HistoricalMod(BaseMod):
    """Module with historical price data support."""
    
    def get_historical_price(self, crypto_id, date):
        """
        Fetch historical price for a specific date.
        
        Args:
            crypto_id: Cryptocurrency ID (e.g., 'bitcoin')
            date: Date in DD-MM-YYYY format
        """
        import requests
        try:
            url = f'https://api.coingecko.com/api/v3/coins/{crypto_id}/history'
            params = {'date': date}
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            price = data.get('market_data', {}).get('current_price', {}).get('usd')
            return f"${price}" if price else "Price not available"
        except Exception as e:
            return f"Error: {str(e)}"

# Usage
mod = HistoricalMod()
price = mod.get_historical_price('bitcoin', '01-01-2024')
print(f"Bitcoin price on Jan 1, 2024: {price}")
```

---

## 🏗️ Architecture

```
BaseMod
├── Core Utilities
│   └── multiply() - Mathematical operations
└── API Integration
    └── get_bittenso_price() - CoinGecko API calls
        ├── HTTP Request
        ├── JSON Parsing
        ├── Error Handling
        └── Response Formatting
```

---

## 🎓 Learning Path

1. **Beginner**: Start with simple price fetching
2. **Intermediate**: Build price monitoring systems
3. **Advanced**: Implement caching, retry logic, and extensions
4. **Expert**: Create production-ready trading bots

---

## 📚 Additional Resources

- [CoinGecko API Documentation](https://www.coingecko.com/en/api/documentation)
- [Python Requests Library](https://requests.readthedocs.io/)
- [JSON Handling in Python](https://docs.python.org/3/library/json.html)
- [Error Handling Best Practices](https://docs.python.org/3/tutorial/errors.html)

---

## 🎯 Next Steps

1. ✅ Complete this tutorial
2. 🔨 Build your first price monitor
3. 🚀 Extend the module with custom features
4. 📊 Create a portfolio tracker
5. 🤖 Build an automated trading system

---

*Crafted with precision, purpose, and passion.* ⚡

**Remember: Simplicity is the ultimate sophistication.** - Leonardo da Vinci
