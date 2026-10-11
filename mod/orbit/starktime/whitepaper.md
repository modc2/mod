# StarkTime: stake time on Starknet

## Abstract

StarkTime is a time-weighted staking protocol for Starknet. A staker locks STRK for a chosen time and receives **weight** equal to the amount times a lock multiplier. A reward stream is split every second pro-rata by weight. Committing for longer earns a larger share. When a lock ends, its weight falls back to 1x, so the boost pays for commitment that is still in force, not commitment that has already expired.

StarkTime ports the StakeTime protocol (EVM, `orbit/staketime`) and its successor BlocTime (`orbit/bloctime`) to Starknet: Cairo instead of Solidity, STRK instead of a native ERC-20, and lapse-to-1x in place of permanent boost.

## Weight

```
weight = amount × multiplier(lock_seconds) / 10000
```

The multiplier is a piecewise-linear curve in basis points. The owner sets it, and it must start at 1x or more and never decrease. Default curve:

- flexible (0d): **1.00x** — unstake at any time
- 7 days: 1.10x
- 30 days: 1.25x
- 90 days: 1.50x
- 180 days: 2.00x
- 1 year: 3.00x
- 2 years: 4.00x
- 4 years (max): **6.00x**

Between points the multiplier is interpolated linearly. A curve change applies only to new stakes and extends. Open positions keep the multiplier they locked in.

## Rewards

Rewards stream continuously through a cumulative **reward-per-weight index** `acc`. While total weight is constant, `acc` grows by `rate × dt / total_weight`. Each position stores `debt = weight × acc` from its last settlement, so the amount it has earned is always:

```
pending = unclaimed + weight × acc − debt
```

The cost of this does not grow with the number of stakers. Every action does O(1) work, and nothing has to loop over all stakers.

**Paper ledger schedule.** One epoch is one day. Epoch `k` emits `max(floor, E0 × (1 − decay)^k)`, with E0 = 10,000 STRK, decay = 0.5% per day and floor = 1,000 STRK per day. Nothing is minted while nobody is staked.

**On-chain schedule.** The owner funds the stream with `notify_reward(amount, duration)`, and the contract pulls those tokens in first. The stream can therefore never promise more than the contract actually holds. A keeper reproduces the paper schedule by notifying once per epoch.

## Lapse

When a lock expires, the position's weight drops to `amount` (1x). It keeps earning at that base rate until it is unstaken, and can be re-boosted with `extend`. Without lapse, a 4-year lock would hold a 6x share forever after unlocking.

- The **paper ledger** processes lapses in time order during accrual, so the index is exact to the second.
- **On chain**, anyone can call `poke(id)` on an expired position to apply its lapse. Until someone does, the position keeps its boosted weight, so keepers (or other stakers, whose share it dilutes) have a reason to poke.

## Actions

- `stake(amount, lock_seconds)`: lock tokens and mint weight. Returns the position id.
- `extend(id, lock_seconds)`: re-lock from now. The new end can never be earlier than the old one.
- `claim(id)` / `claim_all()`: pay out pending rewards.
- `unstake(id)`: after unlock, return principal plus pending rewards.
- `poke(id)`: permissionless lapse of an expired position.
- `notify_reward(amount, duration)` / `set_points(points)`: owner only.

## Native staking

Starknet's native staking (contract `0x00ca17…84a7`) delegates STRK to validators at a flat rate. StarkTime adds the time dimension on top of it. A position can be tagged with a validator address so that weight can be grouped by validator. A future version can forward the locked STRK into native delegation pools and pass those rewards through as well.

## Trust model

- The contract owner can change the curve (for future stakes only) and fund rewards. The owner **cannot** move staked principal. There is no owner withdraw.
- Stake and reward use the same token, and `notify_reward` collects its funds in advance, so the reward stream is always backed.
- The paper ledger is a simulation. Paper STRK has no value, and addresses are not authenticated in paper mode.

## Implementation

- `contracts/src/lib.cairo`: Cairo 2 contract (scarb 2.8.4), tested with snforge.
- `api/src/engine.rs`: the same math as a Rust engine (paper ledger), with unit tests.
- `api/src/starknet.rs`: live Starknet JSON-RPC (chain head, native staking totals, balances).
- `app/`: Next.js static export, served by the Rust binary under `/starktime`.
