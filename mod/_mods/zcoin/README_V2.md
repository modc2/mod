# ZCon Privacy Coin V2 - Enhanced Edition

## 🚀 Major Improvements

### New Features

1. **Merkle Tree Implementation** (`ZconMerkleTree.sol`)
   - Efficient commitment storage using Merkle trees
   - Depth 20 tree supporting 1M+ commitments
   - Historical root tracking for flexible withdrawals
   - Gas-optimized insertion algorithm

2. **Enhanced Privacy Token V2** (`ZconPrivacyTokenV2.sol`)
   - Merkle proof verification for enhanced privacy
   - Better anonymity set through tree structure
   - Improved commitment scheme
   - Support for multiple denominations

3. **Relayer Network** (`ZconRelayer.sol`)
   - Gas-less withdrawals via authorized relayers
   - Configurable relayer fees (max 0.01 ETH)
   - Complete transaction privacy (no direct user interaction)
   - Multi-relayer support with authorization system

### Architecture Improvements

```
┌─────────────────────────────────────────┐
│         ZconPrivacyTokenV2              │
│  ┌───────────────────────────────────┐  │
│  │     Merkle Tree Integration       │  │
│  │  - 20 levels deep                 │  │
│  │  - 1M+ commitment capacity        │  │
│  │  - Historical root tracking       │  │
│  └───────────────────────────────────┘  │
└─────────────────────────────────────────┘
              ↓
┌─────────────────────────────────────────┐
│          ZconRelayer                    │
│  - Gas-less withdrawals                 │
│  - Authorized relayer network           │
│  - Fee management                       │
└─────────────────────────────────────────┘
```

## Quick Start V2

### Deploy V2 Contracts

```bash
# Deploy all V2 contracts
npm run deploy:v2

# Or manually
npx hardhat run scripts/deploy-v2.js --network localhost
```

### Usage Examples

#### Deposit with Merkle Tree

```javascript
const commitment = ethers.keccak256(ethers.toUtf8Bytes("my-secret"));
const amount = ethers.parseEther("1.0");

const tx = await zconTokenV2.deposit(
  commitment,
  ethers.ZeroAddress, // ETH
  amount,
  { value: amount }
);

const receipt = await tx.wait();
const leafIndex = receipt.events[0].args.leafIndex;
console.log("Commitment inserted at index:", leafIndex);
```

#### Withdraw via Relayer (Gas-less)

```javascript
// User generates proof off-chain
const nullifier = ethers.keccak256(ethers.toUtf8Bytes("nullifier"));
const zkProof = "0x..."; // Generated ZK proof
const merkleProof = [...]; // Merkle path proof

// Relayer submits withdrawal
const withdrawalData = zconTokenV2.interface.encodeFunctionData(
  "withdraw",
  [nullifier, root, recipient, commitment, zkProof, merkleProof]
);

await zconRelayer.relayWithdrawal(
  recipient,
  withdrawalData,
  zconTokenV2Address
);
```

## Key Improvements Over V1

| Feature | V1 | V2 |
|---------|----|----||
| Anonymity Set | Limited | 1M+ commitments |
| Gas Efficiency | Moderate | Optimized with Merkle |
| Privacy | Good | Excellent (Merkle + Relayer) |
| Withdrawals | Direct | Gas-less via Relayer |
| Scalability | Limited | High (tree-based) |

## Security Enhancements

1. **Merkle Tree Validation**: All commitments verified through cryptographic proofs
2. **Historical Root Support**: Prevents front-running attacks
3. **Relayer Authorization**: Only approved relayers can process withdrawals
4. **Fee Caps**: Maximum relayer fee prevents exploitation
5. **Reentrancy Protection**: All state-changing functions protected

## Testing V2

```bash
# Run V2 tests
npx hardhat test test/ZconPrivacyTokenV2.test.js

# Run all tests
npm test
```

## Production Considerations

- Integrate real ZK-SNARK library (snarkjs, circom)
- Implement proper Merkle proof generation off-chain
- Set up relayer infrastructure with monitoring
- Add circuit for nullifier/commitment verification
- Implement fee market for relayers
- Add emergency pause functionality

## Gas Optimization

- Merkle tree uses optimized hashing
- Batch deposits supported
- Efficient storage patterns
- Minimal on-chain computation

## License

MIT