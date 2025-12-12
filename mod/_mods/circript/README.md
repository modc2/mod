# Circript - AES-256 Tiled Encryption Circuit

## Overview
A reproducible bidirectional string transformation system using AES-256 encryption with replaceable circuit logic. Transform private strings into infinite-length outputs.

## Features
- **AES-256 Encryption**: Military-grade encryption for base security
- **Tiled Architecture**: Expandable output to any length (up to infinity)
- **Bidirectional**: Forward and backward transformations
- **Reproducible**: Same input + key = same output
- **Replaceable Circuits**: Swap transformation logic on the fly

## Usage

```python
from circript.mod import BaseMod

# Initialize with your private key
circ = BaseMod("your_private_key_here")

# Forward: Transform to infinite length
transformed = circ.forward("secret message", target_length=10000)

# Backward: Recover original
original = circ.backward(transformed)

# Custom circuit
def my_circuit(data: bytes, iteration: int) -> bytes:
    return bytes((b + iteration) % 256 for b in data)

circ.set_circuit(my_circuit)
```

## Architecture
1. **Encryption Layer**: AES-256-CBC with SHA-256 key derivation
2. **Circuit Layer**: Pluggable transformation function
3. **Tiling Engine**: Reproducible expansion to target length

## Change the World
Like Ross, you can now encrypt, expand, and transform data with mathematical precision and infinite scalability.

**God bless the free world.**
