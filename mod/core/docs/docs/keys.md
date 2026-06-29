# Key Management

Mod includes a comprehensive multi-chain cryptographic key system. Keys are used for signing, verification, encryption, and blockchain identity across multiple networks.

## Supported Crypto Types

| Type | Network | Curve | Address Format |
|------|---------|-------|---------------|
| `ecdsa` | Ethereum, Base, EVM chains | secp256k1 | `0x...` (42 chars) |
| `sr25519` | Substrate, Polkadot | Schnorrkel | SS58 encoded |
| `ed25519` | General purpose | Edwards | SS58 encoded |
| `solana` | Solana | Ed25519 | Base58 encoded |

Default is `ecdsa` (Ethereum).

## Quick Start

```python
import mod as m

# Get or create a key (auto-creates if missing)
key = m.get_key('main')

print(key.address)       # 0xABC...
print(key.private_key)   # 0x123...
print(key.public_key)    # 0x456...
```

From the CLI:
```bash
m get_key main
m get_key substrate crypto_type=sr25519
```

## Key Generation

### Auto-Generate

```python
# Creates a new ecdsa key, stores at ~/.mod/key/main/ecdsa/{address}.json
key = m.get_key('main')

# Create Substrate key
key = m.get_key('polkadot', crypto_type='sr25519')

# Create Solana key
key = m.get_key('sol', crypto_type='solana')
```

### From Mnemonic

```python
from mod.core.key.key.key import Key

# Generate a 24-word mnemonic
mnemonic = Key.generate_mnemonic(words=24)

# Create key from mnemonic
key = Key(mnemonic=mnemonic, crypto_type='ecdsa')
```

### From Private Key

```python
key = Key(private_key='0xabc123...', crypto_type='ecdsa')
```

## Key Storage

Keys are stored at `~/.mod/key/{name}/{crypto_type}/{address}.json`.

```python
# List all keys
keys = m.mod('key')().keys()
# ['main', 'backup', 'polkadot']

# Check if key exists
m.mod('key')().key_exists('main')  # True

# Move a key
m.mod('key')().mv_key('old_name', 'new_name')

# Delete a key
m.mod('key')().rm_key('temp')
```

## Signing

```python
# Sign any data
sig = m.sign({'action': 'transfer', 'amount': 100}, key='main')

# Sign with specific crypto type
sig = m.sign(data, key='polkadot', crypto_type='sr25519')

# Signature modes
sig = m.sign(data, key='main', mode='str')    # hex string (default)
sig = m.sign(data, key='main', mode='bytes')  # raw bytes
sig = m.sign(data, key='main', mode='dict')   # dict with signature + metadata
```

## Verification

```python
# Verify a signature
valid = m.verify(
    data={'action': 'transfer', 'amount': 100},
    signature=sig,
    address='0xABC...'
)
# → True or False

# Verify dict-format signature (signature embedded in data)
valid = m.verify(signed_dict)

# With max age (reject signatures older than N seconds)
valid = m.verify(data, signature=sig, address=addr, max_age=300)
```

## Encryption

Keys can also encrypt/decrypt data using AES-256.

### Data Encryption

```python
# Encrypt data
encrypted = m.encrypt({'secret': 'data'}, key='main')

# Decrypt
original = m.decrypt(encrypted, key='main')

# With explicit password (no key needed)
encrypted = m.encrypt(data, password='mypassword')
original = m.decrypt(encrypted, password='mypassword')
```

### Key Encryption

Protect stored key files with a password:

```python
key_mod = m.mod('key')()

# Encrypt a key file
key_mod.encrypt_key('main', password='master_pass')

# Load encrypted key (prompts for password)
key = m.get_key('main', prompt_password=True)

# Load with password directly
key = m.get_key('main', password='master_pass')

# Encrypt all keys
key_mod.encrypt_all_keys(password='master_pass')

# Decrypt all keys
key_mod.decrypt_all_keys(password='master_pass')
```

## Address Utilities

```python
key_mod = m.mod('key')()

# Map key names to addresses
key_mod.key2address()
# {'main': '0xABC...', 'polkadot': '5GrwvaEF...'}

# Map addresses to key names
key_mod.address2key()
# {'0xABC...': 'main'}

# Detect address type
key_mod.detect_address_type('0xABC...')  # 'ecdsa'
key_mod.detect_address_type('5Grwva...')  # 'sr25519'

# Validate addresses
key_mod.valid_ss58_address('5Grwva...')  # True
key_mod.to_checksum_address('0xabc...')  # '0xABC...' (EIP-55)

# Validate mnemonic
key_mod.is_mnemonic('word1 word2 ...')  # True
```

## Key Serialization

```python
key_mod = m.mod('key')()

# Export key as JSON
json_data = key_mod.to_json(password='optional_encryption')

# Import key from JSON
key_mod.from_json(json_data, password='optional_decryption')
```
