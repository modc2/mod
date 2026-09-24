# prefi

Prediction pool on Hyperliquid, Bittensor, Solana and Base: stake on where an asset closes, the pot splits by dollars × accuracy every round. Four ways in — real USDC (pool), free calls, bloctime-weighted agent calls, and the **paper pool**: fake money settled by a local WASM contract in a hash-chained stored log, no live blockchain.

## Capabilities

- **Markets** — list any Hyperliquid pair (perp or spot, ~880), any Bittensor subnet alpha token, and any Solana/Base token over the owner's liquidity floor; CoinGecko for the Base defaults
- **Stake pool** — real USDC/USDT0 on HyperEVM; deposit, signed stakes, weekly rounds, largest-remainder settlement, withdrawals
- **Free play** — no-money calls held out of the pot, priced by a would-have-won counterfactual
- **Agent play (bloctime)** — no dollars down, weighted by value locked on bloctime; agents split a share of each pot's protocol fee
- **Paper pool** — faucet-minted PAPER staked on hourly price calls by any address or agent; settled by the WebAssembly contract `src/paper_contract.wat` (run by the dependency-free interpreter `src/watvm.py`) through the deterministic state machine `src/paper_machine.py`; every message lands in a hash-chained log (`~/.mod/prefi/paper/log.jsonl`) that `paper_verify` replays and proves — auditable balances with no chain
- **Score functions** — the scoring rule is a user-authored expression (sandboxed language in `src/curves.py`), with a signed library, share codes and core/store CIDs
- **PREFI layer** — trade → profit mints PREFI, burn PREFI to call a price, lock for staketime, claim weekly treasury epochs

## Key functions

From config.json `fns` (dispatch is `m prefi/<fn>`):

- `status` / `health` — protocol totals, liveness
- `list_markets`, `seed_hl`, `seed_bt`, `seed_dex`, `add_hl_market`, `add_bt_market`, `add_dex_market` — the asset universe
- `pool_stake`, `pool_free_stake`, `pool_agent_stake`, `pool_settle`, `pool_round`, `pool_leaderboard` — the dollar pool
- `paper_faucet`, `paper_predict`, `paper_transfer`, `paper_resolve` — paper-pool writes
- `paper_status`, `paper_account`, `paper_round`, `paper_leaderboard`, `paper_log`, `paper_verify`, `paper_sign` — paper-pool reads and the audit
- `paper_set_config` — retune the paper pool (host operator, CLI only)
- `predict`, `resolve_predictions`, `prediction_board` — the PREFI burn-to-call layer
- `fn_list`, `fn_test`, `fn_save`, `fn_share`, `fn_publish`, `fn_import` — score functions

## Usage

```python
import mod as m
p = m.mod('prefi')()

# paper pool — the agent loop (fake money, real record)
p.paper_faucet('0xYOU')                          # 1000 PAPER, one grant a day
p.list_markets()                                 # what is callable
p.paper_predict('0xYOU', 'BTC', 115000.0, 25.0)  # stake 25 PAPER on the close
p.paper_resolve()                                # after the close — permissionless
p.paper_account('0xYOU')                         # balance, profit, mean accuracy
p.paper_leaderboard()
p.paper_verify()                                 # replay the whole log, prove the chain
p.paper_transfer('0xYOU', '0xFRIEND', 10.0)      # winnings are distributable

# signed play (optional — flags the record as provably yours)
req = p.paper_sign('paper_faucet', '0xYOU')      # message → wallet personal_sign
p.paper_faucet('0xYOU', signature=sig)

# dollar pool
p.pool_status(); p.pool_round(); p.pool_leaderboard()
```

Agents that only speak HTTP use the same surface: `POST /paper/faucet`, `POST /paper/predict?address=&asset=&price=&stake=`, `POST /paper/resolve`, `GET /paper/leaderboard`, `GET /paper/verify` — see README `## API` for the full table. If prefi is asleep, the activator wakes it at `http://localhost:9000/api/prefi`.

## API

- Local: api `http://localhost:50410`, app `http://localhost:50411/prefi`
- Gateway: `https://modc2.com/prefi/api`, app `https://modc2.com/prefi`

## Environment

- `PREFI_DIR` — store override (default `~/.mod/prefi`); the ledger and the paper log live here
- `PREFI_UNSAFE_NO_SIG=1` — disable signature checks (dev/tests only; refused when a vault key exists)
- `PREFI_PAPER_WASM=0` — run the paper split off the bit-identical Python reference instead of the wasm contract
- `PREFI_HL_API` / `PREFI_HL_WAKE` — hyperliquid module door + activator fallback
- `PREFI_DEX_SNAPSHOTS=0` — switch off the 5-minute DEX price snapshot loop

## Tests

```bash
cd src && python3 -m pytest tests/ -q     # 341, hermetic
```
