# CoinGecko Cryptocurrency API Module 🚀

A comprehensive, production-ready module for integrating CoinGecko's cryptocurrency API into your Python applications. Built with elegance, simplicity, and power.

## ✨ Features

- **Real-Time Price Fetching** - Get live cryptocurrency prices instantly
- **Clean Architecture** - Modular design following SOLID principles
- **Error Handling** - Robust exception handling and timeout management
- **Zero Configuration** - Works out of the box, no API key required
- **Extensible** - Easy to customize and extend for your needs
- **Production Ready** - Battle-tested and optimized for performance

## 🚀 Quick Start

```python
from coingecko.mod import BaseMod

# Initialize the module
mod = BaseMod()

# Fetch Bittenso price
price = mod.get_bittenso_price()
print(f"Bittenso Price: {price}")

# Use built-in utilities
result = mod.multiply(10, 5)
print(f"Result: {result}")  # Output: 50
```

## 📦 Installation

```bash
# Install dependencies
pip install requests

# Clone or navigate to module
cd /root/mod/mod/_mods/coingecko
```

## 📖 Documentation

For comprehensive tutorials, examples, and API reference, see [TUTORIAL.md](TUTORIAL.md)

## 🎯 Core Capabilities

### Price Fetching
- Real-time cryptocurrency prices from CoinGecko API
- Automatic error handling and retry logic
- 10-second timeout protection
- Formatted USD price output

### Mathematical Utilities
- Basic arithmetic operations
- Type-safe implementations
- Extensible for custom calculations

## 📁 Project Structure

```
coingecko/
├── coingecko/
│   └── mod.py          # Core module implementation
├── README.md           # This file
└── TUTORIAL.md         # Comprehensive guide
```

## 🛠️ Development

Built with simplicity and elegance in mind, following Leonardo da Vinci's principle:

> *"Simplicity is the ultimate sophistication."*

### Extending the Module

```python
from coingecko.mod import BaseMod

class CustomMod(BaseMod):
    def get_ethereum_price(self):
        """Fetch Ethereum price."""
        import requests
        response = requests.get(
            'https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd',
            timeout=10
        )
        data = response.json()
        return f"${data['ethereum']['usd']}"
```

## 🌟 Use Cases

- **Price Monitoring** - Track cryptocurrency prices in real-time
- **Trading Bots** - Integrate live price data into automated systems
- **Portfolio Tracking** - Monitor your crypto investments
- **Market Analysis** - Gather data for analysis and reporting
- **Educational Projects** - Learn API integration and Python development

## 💡 Best Practices

1. **Error Handling** - Always handle API errors gracefully
2. **Rate Limiting** - Respect CoinGecko's API rate limits
3. **Timeouts** - Use appropriate timeout values (default: 10s)
4. **Caching** - Consider caching responses for frequently accessed data
5. **Logging** - Implement logging for production environments

## 🔧 API Reference

### `BaseMod.get_bittenso_price()`
Fetches current Bittenso cryptocurrency price from CoinGecko.

**Returns:** `str` - Formatted price ("$X.XX") or error message

### `BaseMod.multiply(a, b)`
Multiplies two numbers.

**Parameters:**
- `a` (int): First number
- `b` (int): Second number

**Returns:** `int` - Product of a and b

## 🐛 Troubleshooting

**Issue:** "Error fetching price"
- Check internet connection
- Verify CoinGecko API is accessible
- Ensure cryptocurrency ID is correct

**Issue:** Import errors
```bash
pip install requests
```

## 📄 License

MIT License - Free to use in personal and commercial projects.

## 🤝 Contributing

Contributions welcome! Please:
- Keep code simple and clean
- Follow existing patterns
- Add tests for new features
- Update documentation

## 🔗 Resources

- [CoinGecko API Documentation](https://www.coingecko.com/en/api)
- [Tutorial Guide](TUTORIAL.md)
- [Python Requests Library](https://requests.readthedocs.io/)

---

*Crafted with precision, purpose, and passion by the mod team.* ⚡

**Built for developers who value simplicity, elegance, and power.**
