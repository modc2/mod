# Bitchain - State Chain on Bitcoin Testnet

A blockchain state machine implementation that stores states in Bitcoin testnet via OP_RETURN metadata.

## Architecture

- **BitchainState**: Represents individual states with data, timestamps, and links to previous states
- **Bitchain**: Main class that manages the state chain and Bitcoin RPC interactions
- Each state is stored in a Bitcoin transaction's OP_RETURN output (max 80 bytes)
- States are linked together forming a verifiable chain

## Features

- Store arbitrary state data in Bitcoin transactions
- Chain states together with cryptographic links
- Verify chain integrity
- Retrieve historical states from the blockchain
- Full Docker setup for easy deployment

## Quick Start

### Prerequisites

- Docker
- Docker Compose

### Running

```bash
# Start the Bitcoin testnet node and bitchain app
docker-compose up --build

# The demo will:
# 1. Wait for Bitcoin node to be ready
# 2. Mine initial blocks
# 3. Create and store 3 states in the blockchain
# 4. Retrieve and verify the states
```

### Manual Testing

```bash
# Enter the bitchain container
docker exec -it bitchain-app bash

# Run the bitchain script
python bitchain.py
```

## How It Works

1. **State Creation**: Create a state with an ID and data payload
2. **Metadata Encoding**: State is serialized to JSON and encoded
3. **Transaction Creation**: Bitcoin transaction with OP_RETURN output containing the metadata
4. **Chain Linking**: Each state references the previous transaction ID
5. **Verification**: Chain integrity can be verified by following the links

## State Structure

```json
{
  "id": "state_001",
  "data": {"action": "init", "value": 100},
  "prev": "previous_txid_or_null",
  "ts": 1234567890
}
```

## Configuration

Environment variables:

- `BITCOIN_RPC_USER`: Bitcoin RPC username (default: bitcoinrpc)
- `BITCOIN_RPC_PASSWORD`: Bitcoin RPC password (default: bitcoinrpcpassword)
- `BITCOIN_RPC_HOST`: Bitcoin node hostname (default: bitcoin-node)
- `BITCOIN_RPC_PORT`: Bitcoin RPC port (default: 18332)

## Limitations

- OP_RETURN data limited to 80 bytes
- Requires Bitcoin testnet coins for transactions
- States must be small enough to fit in metadata

## License

MIT
