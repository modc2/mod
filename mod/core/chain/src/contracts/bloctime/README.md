# BlocTime

Unified staking contract — stake native tokens for a block duration, receive blocTime tokens based on a configurable duration multiplier.

## Features

- Stake native tokens with a specified lock period (in blocks)
- Mint blocTime (ERC20) proportional to stake amount and lock duration
- Configurable multiplier curve via points (linear interpolation)
- Individual stake positions tracked by ID
- Unstake after lock period — returns native tokens, burns blocTime
- `setOwnerless()` for permanent decentralization

## Interface

| Function | Description |
|---|---|
| `stake(amount, lockBlocks)` | Stake native tokens for a block duration |
| `unstake(stakeId)` | Withdraw a completed stake position |
| `getMultiplier(blockCount)` | Get interpolated multiplier for a duration |
| `setPoints(points[])` | Set multiplier curve (owner) |
| `setParams(maxLockBlocks, distributionPct)` | Configure params (owner) |
| `getUserStakeIds(user)` | List stake IDs for a user |
| `getStakePosition(user, stakeId)` | Get position details |

## Test

```sh
npx hardhat test src/contracts/bloctime/test/
```
