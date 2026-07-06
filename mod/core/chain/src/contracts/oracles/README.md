# Oracles

Price feed adapter layer with a standard interface and multiple provider implementations.

## Contracts

| File | Description |
|---|---|
| `IOracleAdapter.sol` | Standard interface — `getPrice(token)` returns `(price, decimals, timestamp)`, `hasPriceFeed(token)` |
| `ManualPriceOracle.sol` | Owner-set prices with batch updates |
| `ChainlinkAdapter.sol` | Chainlink price feed integration |
| `PythAdapter.sol` | Pyth Network price feed integration |

## Adding a New Oracle

Implement `IOracleAdapter`:

```solidity
function getPrice(address token) external view returns (uint256 price, uint8 decimals, uint256 timestamp);
function hasPriceFeed(address token) external view returns (bool exists);
```

Register it via `TokenGate.registerTokenOracle(token, oracle)` or set as default via `TokenGate.setDefaultOracle(oracle)`.

## Test

```sh
npx hardhat test src/contracts/oracles/test/
```
