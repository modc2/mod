# prefi

**Trade, feed the treasury, earn PREFI, burn it to call tomorrow's price, lock
the rest for a weekly cut.**

A trading protocol where profit is not yours to keep — you trade an asset
through the protocol, and when you close in profit that profit goes to the
treasury and you are minted **1 PREFI per $1** captured. That PREFI has two
uses. Lock it for a duration to earn *staketime* (`amount × seconds`) and claim
a pro-rata slice of each weekly treasury epoch. Or **burn** it on a price call:
name where an asset lands one horizon from now (a day, by default), and at
resolution you are scored on how close you got and paid back in fresh PREFI.
Trading is the mint; locking is the claim; predicting is the wager.

```
trade ──profit──► treasury ──weekly epoch──► lockers (pro-rata by staketime)
  │
  └──1 PREFI per $1 profit──► you ──┬──lock──► staketime ──► claim
                                    │
                                    └──burn──► price call ──scored──► PREFI back
```

PREFI has no transfers: it is minted by winning trades and correct calls, and it
leaves by being locked or burned. `prefi_balance()` derives the holding from
those ledgers, and every spend path checks it.

The ledger (markets, positions, predictions, stakes, treasury epochs) is kept
off-tree in `~/.mod/prefi/` — override with `PREFI_DIR`. The Solidity contracts
in `src/contracts/` are the on-chain version of the same rules; nothing is
deployed yet, so the ledger above is the source of truth.

## Scoring

A call is scored on its **normalized dollar error** — the miss in dollars over
the price it was predicting:

```
normalized_error = |predicted − actual| / actual
score            = model(normalized_error, tolerance)     # 0..1
payout           = burn × multiplier × score              # freshly minted PREFI
```

Normalizing is what makes the score comparable across assets: being $640 off on
BTC at $64,000 and $0.004 off on AERO at $0.41 are both a 1% miss and score
identically. The burn is gone the moment the call is placed; a perfect call
returns `multiplier`× it, a total miss returns nothing.

`src/scoring.py` holds the model registry. Adding a curve is one function plus
one entry — nothing else in the protocol knows the names.

| model | curve | shape |
|---|---|---|
| `l2` *(default)* | `1/(1+(err/tol)²)` | inverse-square; never quite zero. `tolerance=1` reproduces `ScoreL2.sol` |
| `linear` | `max(0, 1 − err/tol)` | straight ramp, zero past tolerance |
| `exponential` | `e^(−err/tol)` | 1/e at tolerance, punishes the tail |
| `threshold` | `err ≤ tol ? 1 : 0` | all or nothing |

Parameters live in `~/.mod/prefi/scoring.json` and are settable at runtime:
`model`, `tolerance` (error scale, default 2%), `multiplier` (payout, default
3×), `horizon` (default 86400 = one day), `min_burn`.

```bash
m prefi/set_scoring model=exponential tolerance=0.01 multiplier=5
m prefi/score_preview predicted=64640 actual=64000   # score a hypothetical
```

Params are **snapshotted onto each prediction when it is placed**, so retuning
the score can never re-price a bet already on the table.

Resolution runs lazily on read *and* on demand (`m prefi/resolve_predictions`), and looks up
the price **at the resolve time** rather than at read time — both price sources
answer historically, so a prediction settled three days late still scores the
moment it was due. If neither source can answer, it falls back to spot and marks
the prediction `price_mode: "spot"`.

## Assets

Markets carry a price `source`:

- **`coingecko`** — Base tokens with a Uniswap pool, keyed by `CG_IDS`.
- **`hyperliquid`** — any perp in the HL universe. `add_hl_market` verifies the
  coin exists before listing it, so a typo can't become an unpriceable market.

```bash
m prefi/hl_assets search=SOL      # browse the universe
m prefi/add_hl_market coin=SOL    # list it — priced from HL from then on
```

The console does the same thing from the **Markets → + Hyperliquid** panel.

Hyperliquid rate-limits per IP, and the [`hyperliquid`](../hyperliquid) module
on this host already holds a client against it, so prefi reads HL *through that
module* (`PREFI_HL_API`, default `http://localhost:8919`) and only falls back to
the public endpoint if it isn't running. One HL client per box, not one per
module.

---

## Run it

```bash
m prefi/serve            # api :50410 + app :50411 (production build)
m prefi/serve dev=True   # uvicorn --reload + next dev
m prefi/seed             # list the default Base markets (WETH, cbBTC, AERO)
m prefi/health
```

Under pm2, the way the fleet runs it:

```bash
pm2 start /usr/bin/python3 --name prefi.api -- -m uvicorn api:app \
    --host 0.0.0.0 --port 50410 --app-dir <module>/src/api
pm2 start /usr/bin/npx --name prefi.app --cwd <module>/src/app -- next start -p 50411
```

The app needs a build before `next start`: `cd src/app && npm install && npx next build`.

| | local | gateway |
|---|---|---|
| app | http://localhost:50411/prefi | https://modc2.com/prefi |
| api | http://localhost:50410 | https://modc2.com/api/prefi |

The app is served under `basePath: /prefi` and talks to the API same-origin at
`/prefi/api/*` (a Next rewrite), so one gateway route covers the whole module.

## API

| route | what |
|---|---|
| `GET /health` `GET /status` | liveness, protocol totals |
| `GET /markets` · `POST /markets/add` · `POST /markets/seed` | tradeable assets |
| `GET /hyperliquid/assets` · `POST /hyperliquid/add` | browse and list HL perps |
| `POST /position/open` · `POST /position/close` · `GET /positions/{addr}` | trading |
| `POST /predict` · `GET /predictions[/{addr}]` · `/predictions/board` · `POST /predictions/resolve` | price calls |
| `GET /scoring` · `/scoring/models` · `/scoring/preview` · `POST /scoring` | the score, and its knobs |
| `GET /balance/{addr}` | PREFI minted / burned / locked / available |
| `POST /stake/lock` · `/stake/extend` · `/stake/unlock` · `GET /stakes/{addr}` | staketime |
| `GET /treasury` · `/treasury/history` · `POST /treasury/distribute` · `/treasury/claim` | epochs |
| `GET /leaderboard` · `GET /portfolio/{addr}` · `GET /prices` | read models |

Tests, from `src/`:

```bash
python3 -m pytest tests/ -q                        # 69 cases, hermetic (no network)
python3 -c "from mod import Mod; Mod({}).test()"   # 86-case integration run on a temp ledger
```

## Wallet

Browser wallets (injected / Coinbase) work out of the box. WalletConnect is only
wired in when `NEXT_PUBLIC_WALLETCONNECT_PROJECT_ID` is set — a placeholder id
makes the relay reject the session and takes the whole wallet list down with it.

`NEXT_PUBLIC_UNISWAP_API` is optional: point it at a running
[`uniswap`](../uniswap) module to fill the live pool-price panel.

## Known gaps

- **Docker path is stale.** `Dockerfile` / `docker-entrypoint.sh` build and boot
  the Rust crate in `src/api/` (`prefi-server`), which is an older
  prediction-market API — it does not serve the endpoints this app calls. The
  Python FastAPI in `src/api/api.py` is the real backend. Docker is unbuilt and
  untested; use `m prefi/serve` or pm2.
- Contracts are undeployed — `config.json.contracts` is empty and the ledger is
  server-side only. Nothing is signed: an address is an identity, not a proof,
  so anyone who can reach the API can act as any address.
- **HL prices need a reachable feed.** If the local `hyperliquid` module is down
  *and* the public endpoint is rate-limiting this IP, HL-sourced markets have no
  price: the asset browser comes back empty, `add_hl_market` says so, and a due
  prediction stays open rather than settling against a wrong number.
- Resolution is lazy — a prediction settles when someone next reads it, or when
  `m prefi/resolve_predictions` runs. It scores the price *at* the resolve time
  either way, so lateness costs nothing; run it on a cron if you want the ledger
  to keep itself current.
