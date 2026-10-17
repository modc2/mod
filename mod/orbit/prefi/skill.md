# prefi

Prediction pool on Hyperliquid, Bittensor, Solana and Base: stake on where an asset closes, the pot splits by dollars × accuracy every round. Four ways in — real USDC (pool), free calls, bloctime-weighted agent calls, and the **paper pool**: fake money settled by a local WASM contract in a hash-chained stored log, no live blockchain.

## Capabilities

- **Markets** — list any Hyperliquid pair (perp or spot, ~880), any Bittensor subnet alpha token, and any Solana/Base token over the owner's liquidity floor; CoinGecko for the Base defaults. DEX listings can be narrowed to one venue: anything Raydium quotes lists via `add_raydium_market` / `dex=raydium`, with its own console browser
- **Stake pool** — real USDC/USDT0 on HyperEVM; deposit, signed stakes, weekly rounds, largest-remainder settlement, withdrawals
- **Up / down** — every asset is a card; anyone calls UP or DOWN from the day's open, free or with dollars, the winning side splits the book at the close. The books feed `pool_signal`: bias, conviction and a capped leverage suggestion per asset — the read a leverage desk trades off (nothing here places a trade). Rounds are daily (00:00 UTC)
- **Free play** — no-money calls held out of the pot, priced by a would-have-won counterfactual
- **Agent play (bloctime)** — no dollars down, weighted by value locked on bloctime; agents split a share of each pot's protocol fee
- **Paper pool** — faucet-minted PAPER staked on hourly price calls by any address or agent; settled by the WebAssembly contract `src/paper_contract.wat` (run by the dependency-free interpreter `src/watvm.py`) through the deterministic state machine `src/paper_machine.py`; every message lands in a hash-chained log (`~/.mod/prefi/paper/log.jsonl`) that `paper_verify` replays and proves — auditable balances with no chain
- **Local economy + vibecoded agents** — a simulated market (`src/economy.py`: GBM price feed anchored on live prices, exchange with spread + price impact, one-minute paper pool at `~/.mod/prefi/economy/paper`) where agents (`src/agents.py`) predict and trade every minute. An agent is a sandboxed `decide(ctx)` program written from a sentence by the local claudecode agent (:51260) or a keyword template. **Agents** tab in the console; MCP server at `POST /mcp` (`src/mcp_server.py`, also stdio)
- **Score functions** — the scoring rule is a user-authored expression (sandboxed language in `src/curves.py`), with a signed library, share codes and core/store CIDs
- **PREFI layer** — trade → profit mints PREFI, burn PREFI to call a price, lock for staketime, claim weekly treasury epochs

## Key functions

From config.json `fns` (dispatch is `m prefi/<fn>`):

- `status` / `health` — protocol totals, liveness
- `list_markets`, `seed_hl`, `seed_bt`, `seed_dex`, `add_hl_market`, `add_bt_market`, `add_dex_market` — the asset universe
- `sync_markets` (list EVERYTHING that clears the owner's floors, pause/resume what crosses them; `dry_run=`, `sources=`, `limit=`), `liquidity` (every market's 24h volume vs floor), `liquidity_policy`, `pause_market` / `resume_market` / `remove_market` (owner) — the liquidity layer; floors are pool rules `hl_min_volume_usd`, `bt_min_volume_tao`, `dex_min_volume_usd`, `min_liquidity_usd`, switches `auto_list`, `auto_pause`, `sync_interval`
- `add_raydium_market`, `raydium_assets`, `raydium_stats`, `seed_raydium` — the Raydium venue doors (CLI `add-ray`, `seed-ray`; any `dex-*` action takes `dex=raydium`)
- `pool_stake`, `pool_free_stake`, `pool_agent_stake`, `pool_settle`, `pool_round`, `pool_leaderboard` — the dollar pool
- `pool_side_stake(address, asset, side, amount=0)`, `pool_side_quota`, `pool_side_leaderboard` — up/down wagers; `pool_signal(asset=None)` the crowd signal; `pool_curve(asset, days=30)` daily closes
- `paper_faucet`, `paper_predict`, `paper_transfer`, `paper_resolve` — paper-pool writes
- `paper_status`, `paper_account`, `paper_round`, `paper_leaderboard`, `paper_log`, `paper_verify`, `paper_sign` — paper-pool reads and the audit
- `paper_set_config` — retune the paper pool (host operator, CLI only)
- `predict`, `resolve_predictions`, `prediction_board` — the PREFI burn-to-call layer
- `economy_status`, `economy_prices`, `economy_faucet`, `economy_trade`, `economy_predict`, `economy_account`, `economy_leaderboard`, `economy_round`, `economy_verify` — the simulated economy (play money)
- `economy_set_config`, `economy_reset` — retune / wipe the economy (host operator, CLI only)
- `agent_create` (prompt= vibecodes, code= installs), `agent_list`, `agent_get`, `agent_update` (status=paused|running, code=, revibe=true), `agent_delete`, `agent_run`, `agent_vibecode`, `agent_check`, `agent_language`
- `mcp` — the MCP JSON-RPC door (`m prefi/mcp` prints the tool list and connect strings)
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

# local economy — vibecoded agents, every minute, play money
p.agent_create('dipbuyer', prompt='buy 0.3% below the 5-minute mean, call a bounce halfway back')
p.agent_create('mine', code='def decide(ctx): return [("predict", "BTC", ctx["prices"]["BTC"], 5)]')
p.agent_list(); p.agent_get('dipbuyer')            # log of every minute's actions
p.economy_status(); p.economy_leaderboard()
p.economy_faucet('0xYOU'); p.economy_trade('0xYOU', 'BTC', 'buy', notional=100)

# up/down — the signal a leverage desk reads
p.pool_side_stake('0xYOU', 'BTC', 'up')             # free call, signed in the browser or via pool_sign('side_stake', ...)
p.pool_signal()                                     # per asset: bias long/short/flat, conviction 0..1, leverage ≤ signal_max_leverage
p.pool_curve('BTC', days=30)                        # daily candles for the card
```

MCP: `claude mcp add --transport http prefi http://localhost:50410/mcp` (stdio: `python3 src/mcp_server.py`, same store). Tools are `prefi_*`. Env: `PREFI_ECONOMY=0` stops the clock thread, `PREFI_VIBECODE_LLM=0` forces templates, `PREFI_CLAUDECODE` moves the strategy writer.

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
cd src && python3 -m pytest tests/ -q     # 466, hermetic
```
