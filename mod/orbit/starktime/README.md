# starktime

**StakeTime for Starknet.** Lock STRK for a chosen time. Each position gets
`weight = amount × multiplier(lock)`, which runs from 1x (flexible) to 6x (4 years).
The reward stream is split every second pro-rata by weight. When a lock ends, the
position lapses back to 1x.

One process on **:51280**, route **/starktime**:

| dir | what |
|---|---|
| `api/` | **Rust (axum)**: the protocol engine (paper ledger), live Starknet JSON-RPC, and the server for the built app |
| `app/` | **Next.js** static export (basePath `/starktime`) with STAKE · POSITIONS · BOARD · NETWORK · PROTOCOL tabs |
| `contracts/` | **Cairo 2** `StarkTime` contract with the same math, plus snforge tests |
| `whitepaper.md` | protocol spec (also served at `/whitepaper` and shown in the PROTOCOL tab) |

## Modes

- **Paper ledger (default).** Every address starts with 10,000 paper STRK.
  Stake, extend, claim and unstake all run against `state/ledger.json`. Nothing real moves.
- **Live reads.** These are real Starknet mainnet data: chain head, totals from the native staking
  contract `0x00ca17…84a7`, and STRK/ETH balances for any address.
- **On-chain.** Deploy `contracts/` with `sncast`, then set `config.json` `contract.address`.
  `/contract` then reads `get_stats` live.

## Use

```bash
m starktime/build      # app export -> dist (atomic), cargo release, scarb build
m starktime/serve      # pm2 "starktime" -> http://localhost:51280/starktime/
m starktime/test       # cargo test + snforge test + live /test

m starktime/quote amount=1000 lock_days=365
m starktime/stake owner=0xabc amount=500 lock_days=90
m starktime/account owner=0xabc
m starktime/claim owner=0xabc
m starktime/network
```

## API (`/starktime/_api/*`, also `/api`, `/_api`, bare)

GET `info params quote curve emissions stats account positions leaderboard
validators history network balance contract whitepaper test`
POST `stake unstake extend claim`

Errors are always 400 JSON, never 5xx.

## Contract

```bash
cd contracts
scarb build
snforge test           # 9 tests: curve, 3:1 split, locks, ownership, payout, poke, extend, set_points
```

Entrypoints: `stake unstake extend claim claim_all poke notify_reward set_points`
and the views `get_position positions_of pending get_multiplier get_points get_stats get_reward_period`.
The owner can change the curve (new stakes only) and fund rewards. The owner
**cannot** withdraw staked principal.
