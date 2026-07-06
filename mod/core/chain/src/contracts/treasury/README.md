# Treasury

Distributes treasury balance proportionally to governance token holders. Supports multiple whitelisted tokens via TokenGate.

## Features

- Proportional distribution based on governance token ownership
- Owner percentage allocation (basis points)
- Supports all TokenGate-whitelisted tokens
- Withdraw by specific token or withdraw all
- Uses current balance only (no historical accounting)
- `setOwnerless()` for permanent decentralization

## Interface

| Function | Description |
|---|---|
| `fund(token, amount)` | Deposit tokens into treasury |
| `withdraw(token)` | Claim proportional share of a token |
| `withdrawAll()` | Claim share of all whitelisted tokens |
| `ownerWithdraw(token, amount)` | Owner claims their allocation |
| `setGovernanceToken(token)` | Set governance token (owner) |
| `setOwnerPercentage(bps)` | Set owner share in basis points |

## Test

```sh
npx hardhat test src/contracts/treasury/test/
```
