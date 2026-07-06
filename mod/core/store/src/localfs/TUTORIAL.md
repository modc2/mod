# BaseMod Tutorial 🎓

## What is BaseMod?

BaseMod is a foundational Python module that provides core functionality and utilities for building elegant, modular systems. It's designed with simplicity and extensibility in mind.

## 📚 Table of Contents

1. [Overview](#overview)
2. [Installation](#installation)
3. [Core Features](#core-features)
4. [Usage Examples](#usage-examples)
5. [API Reference](#api-reference)

## Overview

The BaseMod class serves as a template for building modular systems. It currently includes:
- Basic mathematical operations
- Cryptocurrency price fetching capabilities
- Clean, extensible architecture

## Installation

```bash
# Clone or navigate to the base module directory
cd /root/mod/mod/_mods/base

# Install dependencies
pip install requests
```

## Core Features

### 1. Mathematical Operations

BaseMod provides simple mathematical utilities:

```python
from base.mod import BaseMod

# Create an instance
mod = BaseMod()

# Multiply two numbers
result = mod.multiply(5, 10)
print(result)  # Output: 50
```

### 2. Cryptocurrency Price Fetching

Fetch real-time cryptocurrency prices from CoinGecko API:

```python
from base.mod import BaseMod

mod = BaseMod()

# Get Bittenso price
price = mod.get_bittenso_price()
print(f"Current Bittenso price: {price}")
# Output: Current Bittenso price: $0.00123 (example)
```

## Usage Examples

### Example 1: Basic Math Operations

```python
from base.mod import BaseMod

def calculate_area(length, width):
    mod = BaseMod()
    return mod.multiply(length, width)

area = calculate_area(15, 20)
print(f"Area: {area} square units")  # Output: Area: 300 square units
```

### Example 2: Price Monitoring

```python
from base.mod import BaseMod
import time

def monitor_price(interval=60):
    """Monitor cryptocurrency price at regular intervals."""
    mod = BaseMod()
    
    while True:
        price = mod.get_bittenso_price()
        print(f"[{time.strftime('%H:%M:%S')}] Bittenso: {price}")
        time.sleep(interval)

# Run price monitoring (checks every 60 seconds)
monitor_price()
```

### Example 3: Error Handling

```python
from base.mod import BaseMod

mod = BaseMod()

# The get_bittenso_price method handles errors gracefully
price = mod.get_bittenso_price()

if "Error" in price:
    print(f"Failed to fetch price: {price}")
else:
    print(f"Successfully fetched price: {price}")
```

## API Reference

### Class: `BaseMod`

Base module providing core functionality and utilities.

#### Methods

##### `multiply(a: int, b: int) -> int`

Multiply two numbers and return the result.

**Parameters:**
- `a` (int): First number
- `b` (int): Second number

**Returns:**
- `int`: Product of a and b

**Example:**
```python
mod = BaseMod()
result = mod.multiply(7, 8)
print(result)  # 56
```

##### `get_bittenso_price() -> str`

Fetch the current price of Bittenso cryptocurrency from CoinGecko API.

**Parameters:**
- None

**Returns:**
- `str`: Current USD price (formatted as "$X.XX") or error message

**Example:**
```python
mod = BaseMod()
price = mod.get_bittenso_price()
print(price)  # "$0.00123" or "Error fetching price: ..."
```

**Error Handling:**
- Returns error message if API request fails
- Returns error message if network timeout occurs
- Returns error message for unexpected exceptions

## 🏗️ Architecture

```
BaseMod
├── Core Utilities
│   └── multiply() - Mathematical operations
└── External Integrations
    └── get_bittenso_price() - CoinGecko API integration
```

## 🔧 Extending BaseMod

You can easily extend BaseMod with your own methods:

```python
from base.mod import BaseMod

class MyCustomMod(BaseMod):
    """Extended version of BaseMod with custom functionality."""
    
    def divide(self, a: int, b: int) -> float:
        """Divide two numbers."""
        if b == 0:
            raise ValueError("Cannot divide by zero")
        return a / b
    
    def get_ethereum_price(self) -> str:
        """Fetch Ethereum price."""
        import requests
        try:
            response = requests.get(
                'https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd',
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            price = data.get('ethereum', {}).get('usd', 'Price not available')
            return f"${price}" if isinstance(price, (int, float)) else price
        except Exception as e:
            return f"Error: {str(e)}"

# Usage
custom_mod = MyCustomMod()
print(custom_mod.divide(100, 4))  # 25.0
print(custom_mod.get_ethereum_price())  # "$2500.00" (example)
```

## 💡 Best Practices

1. **Error Handling**: Always handle potential errors when using external APIs
2. **Timeouts**: The API calls include 10-second timeouts to prevent hanging
3. **Modularity**: Extend the class rather than modifying the base implementation
4. **Type Hints**: Use type hints for better code clarity and IDE support

## 🐛 Troubleshooting

### Issue: "Error fetching price"

**Solution:**
- Check your internet connection
- Verify the CoinGecko API is accessible
- Ensure the cryptocurrency ID is correct

### Issue: Import errors

**Solution:**
```bash
# Install required dependencies
pip install requests
```

## 📝 Notes

- Built following SOLID principles
- Designed for simplicity and elegance
- Production-ready and battle-tested
- Follows Leonardo da Vinci's principle: *"Simplicity is the ultimate sophistication."*

## 🚀 Next Steps

1. Explore the source code in `base/mod.py`
2. Read the main `README.md` for project overview
3. Extend BaseMod with your own custom methods
4. Build amazing modular systems!

---

*Crafted with precision, purpose, and passion.* ⚡
