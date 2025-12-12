# ZCon Privacy Coin

A privacy-focused ERC20-compatible token on EVM using zero-knowledge proofs with authorized oracle functionality.

## Features

- **Privacy Transactions**: Deposit and withdraw any ERC20 token (including ETH) with ZK proof privacy
- **Commitment Scheme**: Uses cryptographic commitments to hide transaction details
- **Nullifier System**: Prevents double-spending while maintaining privacy
- **Authorized Oracles**: ZK-proof verified oracle system for storing sensitive data
- **EVM Compatible**: Works on any EVM-compatible blockchain

## Architecture

### ZconPrivacyToken Contract
- Handles private deposits and withdrawals
- Supports ETH and any ERC20 token
- Uses commitment/nullifier scheme for privacy
- Integrates ZK proof verification

### ZconOracle Contract
- Authorized oracle system with access control
- Stores data with ZK proof verification
- Timestamped and verifiable data storage

## Quick Start

### Prerequisites
- Docker
- Docker Compose

### Installation & Deployment

```bash
# Clone and navigate to directory
cd /Users/homie/mod/mod/_mods/zcoin

# Build and start containers
docker-compose up --build

# The Hardhat node will start on localhost:8545
# Contracts will be automatically deployed
```

### Manual Deployment

```bash
# Install dependencies
npm install

# Compile contracts
npm run compile

# Start local node
npm run node

# Deploy (in another terminal)
npm run deploy
```

## Usage

### Deposit (Private)

```javascript
const commitment = ethers.utils.keccak256(ethers.utils.toUtf8Bytes("secret"));
await zconToken.deposit(tokenAddress, amount, commitment, { value: amount });
```

### Withdraw (Private)

```javascript
const nullifier = ethers.utils.keccak256(ethers.utils.toUtf8Bytes("nullifier"));
const zkProof = "0x..."; // Generate ZK proof
await zconToken.withdraw(nullifier, commitment, recipient, zkProof);
```

### Oracle Data Storage

```javascript
// Authorize oracle
await zconOracle.authorizeOracle(oracleAddress);

// Store data with ZK proof
const dataHash = ethers.utils.keccak256(data);
await zconOracle.storeData(key, dataHash, zkProof);
```

## Docker Commands

```bash
# Start services
docker-compose up -d

# View logs
docker-compose logs -f

# Stop services
docker-compose down

# Rebuild
docker-compose up --build
```

## Security Considerations

- The ZK proof verification is simplified for demonstration
- For production, integrate with proper ZK libraries (snarkjs, circom, etc.)
- Implement proper key management
- Audit smart contracts before mainnet deployment

## Future Enhancements

- Integration with Circom/snarkjs for real ZK proofs
- Merkle tree for commitment storage
- Multi-token pool support
- Relayer network for gas-less withdrawals
- Advanced oracle aggregation

## License

MIT
