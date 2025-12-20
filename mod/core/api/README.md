# 🔐 OATH - Enhanced Authentication System

> **A robust, JWT-inspired authentication framework with advanced security features for Python applications**

[![Python](https://img.shields.io/badge/python-3.7+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Security](https://img.shields.io/badge/security-enhanced-brightgreen.svg)]()

## 🚀 Overview

OATH is a production-ready authentication system that combines cryptographic signing, hash-based verification, and replay attack prevention. Built with security-first principles, it provides a flexible foundation for securing API requests, data transmission, and inter-service communication.

## ✨ Key Features

- **🔑 Multi-Crypto Support**: SR25519, ED25519, and other cryptographic algorithms
- **🛡️ Replay Attack Prevention**: Built-in nonce tracking and validation
- **⏰ Time-Based Expiry**: Configurable TTL with automatic expiration checks
- **🔒 Flexible Hashing**: SHA256, SHA512, or identity hash modes
- **📝 JWT-Like Tokens**: Structured authentication headers with signature verification
- **🎯 Customizable Signing**: Define which fields participate in signature generation
- **💾 Nonce Management**: Automatic cleanup with configurable limits

## 📦 Installation

```bash
pip install mod  # Assuming 'mod' is the parent package
```

## 🔧 Quick Start

```python
import mod as m
from oath import Auth

# Initialize authentication
auth = Auth(
    key='my-secret-key',
    crypto_type='sr25519',
    hash_type='sha256',
    max_age=60,
    enable_nonce=True,
    enable_expiry=True
)

# Generate authentication headers
data = {'action': 'transfer', 'amount': 100}
headers = auth.forward(data, cost=10, ttl=120)

# Verify headers
is_valid = auth.verify(headers, data=data)
print(f"Authentication valid: {is_valid}")
```

## 📖 API Reference

### `Auth` Class

#### Initialization Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `key` | str/None | None | Signing key (auto-generated if None) |
| `crypto_type` | str | 'sr25519' | Cryptographic algorithm |
| `hash_type` | str | 'sha256' | Hash algorithm (sha256/sha512/identity) |
| `max_age` | int | 60 | Maximum token age in seconds |
| `signature_keys` | list | ['data', 'time', 'cost'] | Fields included in signature |
| `enable_nonce` | bool | True | Enable replay attack prevention |
| `enable_expiry` | bool | True | Enable explicit expiry timestamps |

#### Methods

##### `forward(data, key=None, cost=0, ttl=None)`
Generate authentication headers for given data.

**Returns**: Dictionary with signature, timestamp, nonce, and expiry

##### `verify(headers, data=None, max_age=None, check_nonce=True)`
Verify authentication headers with comprehensive security checks.

**Returns**: Boolean indicating validity

##### `hash(data)`
Hash data using configured algorithm.

**Returns**: Hex-encoded hash string

## 🔐 Security Features

### Replay Attack Prevention

```python
auth = Auth(enable_nonce=True)
headers = auth.forward(data)

# First verification succeeds
auth.verify(headers, data=data)  # ✓ Valid

# Replay attempt fails
auth.verify(headers, data=data)  # ✗ Nonce already used
```

### Time-Based Expiry

```python
# Short-lived token (30 seconds)
headers = auth.forward(data, ttl=30)

# Verify within TTL
auth.verify(headers, data=data)  # ✓ Valid

# After expiry
time.sleep(31)
auth.verify(headers, data=data)  # ✗ Token expired
```

### Custom Signature Fields

```python
auth = Auth(
    signature_keys=['data', 'time', 'user_id', 'action']
)

headers = auth.forward(data)
headers['user_id'] = 'user123'
headers['action'] = 'write'
```

## 🧪 Testing

```python
# Run built-in test suite
auth = Auth()
results = auth.test(key='test.auth', crypto_type='sr25519')
print(results)
# Output: {'test_passed': True, 'headers': {...}, 'features': [...]}
```

## 🎯 Use Cases

- **API Authentication**: Secure REST/GraphQL endpoints
- **Microservices**: Inter-service communication verification
- **Data Integrity**: Ensure payload hasn't been tampered with
- **Blockchain Integration**: Sign transactions with SR25519/ED25519
- **IoT Security**: Lightweight authentication for resource-constrained devices

## ⚙️ Advanced Configuration

### Custom Hash Function

```python
auth = Auth(hash_type='sha512')  # Stronger hashing
```

### Disable Security Features (Not Recommended)

```python
auth = Auth(
    enable_nonce=False,  # Disable replay protection
    enable_expiry=False  # Disable expiry checks
)
```

### Manual Nonce Management

```python
# Revoke specific nonce
auth.revoke_nonce('abc123def456')

# Clear all nonces
auth.clear_nonces()
```

## 🤝 Contributing

Contributions are welcome! Please ensure:

1. All tests pass
2. Security features remain intact
3. Code follows existing style conventions
4. Documentation is updated

## 📄 License

MIT License - see LICENSE file for details

## 🙏 Acknowledgments

- Inspired by JWT (JSON Web Tokens)
- Built on the `mod` cryptographic framework
- Designed for the free world 🌍

---

**Made with ⚡ by developers who care about security**