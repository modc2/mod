# Prediction Market Smart Contract

## Overview

Ethereum smart contract for 24-hour prediction markets. Users lock liquidity to predict whether an asset will go UP or DOWN in the next 24 hours. Profitable traders are rewarded from the losing pool.

## Features

- ✅ Lock liquidity for 24-hour predictions
- ✅ Support for any asset (BTC, ETH, etc.)
- ✅ Automatic settlement after 24 hours
- ✅ Proportional reward distribution (95% of losing pool)
- ✅ Multi-user support with individual balances
- ✅ Tested on Ganache and Base network

## Contract Structure

### Main Functions

1. **deposit()** - Deposit ETH to your balance
2. **openPosition()** - Open a 24-hour prediction position
3. **settlePosition()** - Settle position after 24 hours
4. **distributeRewards()** - Distribute rewards to winners
5. **withdraw()** - Withdraw available balance

### Position Flow

```
1. User deposits ETH → Balance increased
2. User opens position → Liquidity locked for 24h
3. After 24h → Position can be settled
4. Settle position → Mark as profitable/unprofitable
5. Distribute rewards → Winners get share of losing pool
6. Withdraw → User claims profits
```

## Testing on Ganache (Local)

### Prerequisites

```bash
npm install -g ganache
npm install web3 solc
```

### Start Ganache

```bash
ganache --deterministic --accounts 10 --defaultBalanceEther 100
```

### Run Tests

```bash
cd test
node test_ganache.js
```

### Expected Output

```
=== Prediction Market Smart Contract Test on Ganache ===

Available accounts: 10
Deployer account: 0x...
Compiling contract...
Deploying contract...
Contract deployed at: 0x...

--- Test 1: Deposit Funds ---
User balance: 10 ETH

--- Test 2: Open Position ---
Position opened: 0x...

--- Test 3: Position Details ---
User: 0x...
Amount: 1 ETH
Direction: UP
Entry Price: 50000
Asset: BTC

--- Test 4: Fast Forward 24 Hours ---
Time advanced by 24 hours

--- Test 5: Settle Position ---
Position settled
Is profitable: true

--- Test 6: Distribute Rewards ---
Rewards distributed
Final balance: 10 ETH

=== All Tests Completed Successfully ===
```

## Testing on Base Network

### Prerequisites

1. Get Base Goerli testnet ETH from faucet:
   - https://www.coinbase.com/faucets/base-ethereum-goerli-faucet

2. Set your private key:
   ```bash
   export PRIVATE_KEY="your_private_key_here"
   ```

### Deploy to Base Goerli

```bash
cd test
node test_base.js
```

### Network Information

- **Network Name**: Base Goerli
- **RPC URL**: https://goerli.base.org
- **Chain ID**: 84531
- **Currency**: ETH
- **Block Explorer**: https://goerli.basescan.org

### Add to MetaMask

1. Open MetaMask
2. Networks → Add Network
3. Enter Base Goerli details above
4. Save

## Usage Examples

### Example 1: Simple Prediction

```javascript
// 1. Deposit funds
await contract.methods.deposit().send({ 
    from: userAddress, 
    value: web3.utils.toWei('1', 'ether') 
});

// 2. Open UP position on BTC
const tx = await contract.methods.openPosition(
    web3.utils.toWei('0.5', 'ether'), // Amount
    0, // Direction.UP
    50000, // Current BTC price
    'BTC' // Asset
).send({ from: userAddress });

const positionId = tx.events.PositionOpened.returnValues.positionId;

// 3. Wait 24 hours...

// 4. Settle position
await contract.methods.settlePosition(
    positionId,
    55000 // Exit price (BTC went up!)
).send({ from: userAddress });

// 5. Distribute rewards
await contract.methods.distributeRewards('BTC')
    .send({ from: userAddress });

// 6. Withdraw profits
const balance = await contract.methods.getBalance(userAddress).call();
await contract.methods.withdraw(balance).send({ from: userAddress });
```

### Example 2: Multiple Users

```javascript
// User A predicts UP
await contract.methods.openPosition(
    web3.utils.toWei('1', 'ether'),
    0, // UP
    50000,
    'BTC'
).send({ from: userA });

// User B predicts DOWN
await contract.methods.openPosition(
    web3.utils.toWei('1', 'ether'),
    1, // DOWN
    50000,
    'BTC'
).send({ from: userB });

// After 24h, BTC is at 52000 (went UP)
// User A wins, User B loses
// User A gets their 1 ETH + 95% of User B's 1 ETH = 1.95 ETH
```

## Contract Deployment

### Using Hardhat

```javascript
// hardhat.config.js
module.exports = {
  solidity: "0.8.0",
  networks: {
    baseGoerli: {
      url: "https://goerli.base.org",
      accounts: [process.env.PRIVATE_KEY]
    }
  }
};
```

```bash
npx hardhat run scripts/deploy.js --network baseGoerli
```

### Using Remix

1. Go to https://remix.ethereum.org
2. Create new file: `PredictionMarket.sol`
3. Paste contract code
4. Compile with Solidity 0.8.0
5. Deploy to Base Goerli via MetaMask

## Security Considerations

⚠️ **Important**: This is a demonstration contract. For production:

1. Add access controls (OpenZeppelin Ownable)
2. Implement oracle integration for price feeds (Chainlink)
3. Add reentrancy guards
4. Implement pause mechanism
5. Add comprehensive testing
6. Get professional audit

## Gas Optimization

- Opening position: ~150,000 gas
- Settling position: ~100,000 gas
- Distributing rewards: ~200,000 gas (varies with position count)

## Troubleshooting

### "Insufficient balance" error
- Make sure you deposited funds first
- Check balance: `contract.methods.getBalance(address).call()`

### "Position cannot be settled before exit time"
- Wait full 24 hours from position opening
- On Ganache, use `evm_increaseTime` to fast forward

### Transaction fails on Base
- Ensure you have enough ETH for gas
- Check gas price is not too low
- Verify network is Base Goerli (Chain ID: 84531)

## License

MIT License - See contract header

## Support

For issues or questions:
1. Check existing positions: `getActivePositionsCount()`
2. Verify contract state on BaseScan
3. Review transaction logs for errors

---

**Built with ❤️ for DeFi innovation**