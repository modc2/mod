# TokenGate

Token whitelist with per-token oracle adapter registry. Used by Market and Treasury for token validation and price feeds.

## Features

- Default oracle + per-token oracle overrides
- Whitelist/delist tokens (requires oracle price feed)
- Batch whitelist operations
- Efficient swap-and-pop deletion with full storage cleanup
- `setOwnerless()` for permanent decentralization

## Interface

| Function | Description |
|---|---|
| `whitelistToken(token)` | Add token to whitelist (requires price feed) |
| `batchWhitelistTokens(tokens[])` | Batch whitelist |
| `delistToken(token)` | Remove token + cleanup storage |
| `registerTokenOracle(token, oracle)` | Set token-specific oracle |
| `removeTokenOracle(token)` | Fall back to default oracle |
| `getTokenPrice(token)` | Get price from registered oracle |
| `isTokenWhitelisted(token)` | Check whitelist status |
| `getTokenList()` | List all whitelisted tokens |

## Test

```sh
npx hardhat test src/contracts/tokengate/test/
```
