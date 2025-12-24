# Store Module

A lightweight, persistent key-value store with Docker support.

## Overview

The Store module provides a simple yet powerful way to persist data across sessions. Built with simplicity in mind, following the Leonardo da Vinci principle: *"Simplicity is the ultimate sophistication."*

## Features

- 🔑 **Key-Value Storage** - Simple get/set operations
- 💾 **Persistence** - Data survives restarts
- 🐳 **Docker Ready** - Containerized deployment
- 🧪 **Tested** - Comprehensive test suite

## Quick Start

```python
from store import Store

# Initialize
store = Store()

# Set a value
store.set('key', 'value')

# Get a value
value = store.get('key')
```

## Docker Deployment

```bash
# Start the store service
docker-compose up -d

# Stop the service
docker-compose down
```

## Project Structure

```
store/
├── store.py              # Core store implementation
├── docker-compose.yml    # Docker configuration
├── test/
│   └── test.py          # Test suite
└── README.md            # This file
```

## Testing

```bash
python test/test.py
```

## License

MIT

---

*Built with ❤️ by the mod team*