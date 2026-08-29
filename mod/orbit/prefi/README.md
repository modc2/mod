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

## The stake pool — real money, on Hyperliquid's EVM

Everything above is PREFI, an internal token this server mints on its own say-so.
The **pool** is the other half: real USDC and USDT0, deposited on
[HyperEVM](https://hyperevmscan.io) (chain 999), staked on where an asset closes,
and paid out on chain.

```
deposit USDC/USDT0 ──► balance ──stake $N on a price──► the round's pot
                                                             │
              Hyperliquid mark at the close ──scores it──────┤
                                                             ▼
                       payout = pot × score / Σscores ──► balance ──► withdraw
```

**The rule.** Each entry is scored on its relative L1 error against the closing
mark, weighted by the dollars behind it:

```
relative L1 error   e = |called − actual| / actual
accuracy            a = model(e, tolerance)          # 1 dead on, 0 hopeless
score               s = dollars × a
payout                = pot × s / Σs
```

The default is `linear` at `tolerance = 1.0`, which is exactly `a = 1 − e` — a
pure relative-L1 score with no curve on top. Being twice as close is worth twice
as much, and so is staking twice as much. Sharpen it by lowering the tolerance
(`0.02` → only calls inside 2% score at all) or swap the curve for `l2`,
`exponential` or `threshold`. If *nobody* scores above zero, every stake is
refunded and the protocol takes no fee — losing the pot because the week was
hard is not a rule anyone would agree to in advance.

**Rounds are weekly, and the owner sets that.** `interval=604800` is the
default; `m prefi/pool-set interval=86400` makes it daily. A new interval takes
effect at the *next* boundary, so the round people have already staked into keeps
the length it was sold with — and its scoring params are frozen from the moment
it opened, so a retune can never re-price an open bet. Entries stop
`entry_cutoff` (1h) before the close, which is what stops a stake placed once the
answer is already known.

**One pot per asset.** Normalized error is comparable across assets, which
tempts you into a single pot — but then anyone can call a stablecoin at $1.00 for
a guaranteed ~0 error and drain the BTC stakers. Pots are keyed (round, asset)
and only ever pay their own stakers. Pool markets must be Hyperliquid-priced
(`m prefi/add-hl coin=BTC`), because the HL mark at the close is the oracle.

### Free calls

You do not need a deposit to play. Every address gets `free_per_round` calls a
round (3 by default, one per asset), signed like any other but backed by no
money at all:

```bash
m prefi/free-stake address=0x… asset=BTC price=80000    # costs nothing
m prefi/free-quota address=0x…                          # what's left this round
m prefi/free-board                                      # free callers by accuracy
```

A free call **never enters the pot** — it is held out of the settlement
entirely, not staked at $0. That is a structural guarantee rather than an
arithmetic accident: no staker's payout can be diluted by someone who risked
nothing, and no free call writes a row to the ledger, because the ledger is
money and this is not.

What it gets instead is `would_win`: what the same call *would* have taken had
it been staked `free_notional` ($100 by default), computed against the pot that
actually formed. The counterfactual includes the caller's own money, because
staking moves the pot as well as the split — "if I had put $100 in, what would
have come back out" is the honest question, and "what could I have skimmed off a
pot I never funded" is a bigger number and a lie. A test asserts the shadow
number matches a real settlement run to the micro-dollar, across every scoring
model and fee, so what a free player is shown is what they would have been paid.

Two caps, for one reason: one call per asset per round and `free_per_round`
overall. A free player who could place ten calls on BTC at ten different prices
would have a meaningless accuracy and a would-have-won number advertising a bet
nobody could have placed. Free callers rank on their own board — the paid board
ranks by dollars won, and a free caller has won none. `m prefi/pool-set
free_per_round=0` switches the whole thing off.

### Money in, money out

Deposits are plain ERC-20 transfers to the vault address. Two ways they get
credited, and both land on the same idempotent `(tx, log_index)` key so a deposit
found twice is credited once:

- **By hash** — one `eth_getTransactionReceipt`, instant. This is what the UI
  does the moment your wallet returns a hash.
- **By sweep** — `m prefi/pool-sync` walks Transfer logs into the vault from a
  persisted cursor. Hyperliquid's own RPC rate-limits `eth_getLogs` into
  uselessness from a shared host, so the scan falls back through community
  endpoints (`PREFI_HYPEREVM_LOG_RPC` to override) and stops 25 blocks short of
  the tip, since those nodes run a little behind.

Withdrawals go back to the depositing address — there is no "withdraw to"
parameter, so a stolen signature cannot redirect funds. With a hot key present
and `auto_pay` on they send immediately; otherwise they queue for an owner to
release (`m prefi/pay id=1`) or to pay by hand and record
(`m prefi/mark-paid id=1 tx=0x…`). A send that fails puts the money back.

**Spending is signed.** Staking and withdrawing require an EIP-191
`personal_sign` over readable text, bound to a per-address nonce so a signature
cannot be replayed, cannot be spent as a different action, and cannot be altered
after signing. `GET /pool/sign` returns the exact message the server will rebuild
when it checks. `PREFI_UNSAFE_NO_SIG=1` turns the check off for local work.

**Custody, stated plainly.** `m prefi/pool-create-vault` generates a hot wallet
whose key lives at `~/.mod/prefi/hyperevm_key.json` — this server can move
depositor funds. `m prefi/pool-set-vault address=0x…` points the pool at an
address you control instead; deposits still credit, withdrawals just queue.
`GET /pool/vault` reports what the wallet holds against what the ledger owes,
so under-collateralisation is visible rather than discovered.

```bash
m prefi/pool-create-vault           # or pool-set-vault address=0x…
m prefi/pool-tokens verify=true     # re-read symbol/decimals off the chain
m prefi/add-hl coin=BTC             # a market the pool can settle
m prefi/pool-claim address=0x…      # take ownership, prints the owner secret
m prefi/pool-set interval=604800 fee_bps=100 secret=…
m prefi/deposit tx=0x…              # credit a deposit
m prefi/round                       # the pot, with live provisional scores
m prefi/free-stake address=0x… asset=BTC price=80000   # no deposit needed
m prefi/settle                      # settle closed rounds (safe on a cron)
```

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
- **`hyperliquid`** — **every pair Hyperliquid quotes**: ~180 perps and ~700
  spot books, about 880 markets in all. `add_hl_market` verifies the pair
  exists before listing it, so a typo can't become an unpriceable market.

```bash
m prefi/hl_stats                      # 878 pairs · 176 perps · 702 spot
m prefi/hl_assets search=HYPE         # every HYPE market, liquid end first
m prefi/hl_assets kind=spot limit=0   # all 702 spot pairs
m prefi/add_hl_market coin=SOL        # a perp
m prefi/add_hl_market coin=HYPE/USDC  # a spot pair
m prefi/add_hl_market coin=@107       # ...the same pair, by HL's own key
m prefi/seed_hl limit=20              # or list the 20 busiest at once
m prefi/seed_hl kind=spot limit=10 min_volume=1000000
```

`seed_hl` takes the *top* of the volume ranking rather than a count of new
listings, so running it twice lists nothing the second time. The console has the
same thing as the **top 20** button in the pair panel.

The console does the same thing from the **Markets → + Hyperliquid** panel: a
search over the whole universe with PERP/SPOT tabs, 24h volume and change per
row, sorted so the liquid end is what you see first.

### The two names of a pair

HL names perps and spot books differently, and the difference is the only thing
that makes this awkward. A perp is its own name — `BTC`. A spot book is quoted
under an **`@index` key** — `HYPE/USDC` is `@107` to `allMids` and to
`candleSnapshot`, and only `spotMeta` says which token that index is. So a
market records **`hl_key`** at listing time, and every price and settlement
lookup goes through it. Three consequences worth knowing:

- A spot market's symbol carries a slash (`HYPE/USDC`), so asset params are URL
  encoded and `GET /prices/{asset}` is a `:path` route.
- HL quotes ~700 spot pairs and *names* ~330 of them. The unnamed ones stay
  listable under their raw `@index` key rather than disappearing — they price
  and settle exactly the same.
- Prediction legs (`#12000`) are dropped. They are event odds, not a pair a
  price call can be settled against. Delisted perps are dropped too: still
  quoted, no longer tradeable.

Hyperliquid rate-limits per IP, and the [`hyperliquid`](../hyperliquid) module
on this host already holds a client against it, so prefi reads HL *through that
module* (`PREFI_HL_API`, default `http://localhost:8919`) and only falls back to
the public endpoint if it isn't running. One HL client per box, not one per
module. That module is **scale-to-zero**, so a refused call on its own port
means *asleep*, not *no feed* — prefi retries through the activator
(`PREFI_HL_WAKE`, default `http://localhost:9000/api/hyperliquid`), which is the
door that wakes it. The pair list itself is cached 15 minutes in memory and on
disk (`~/.mod/prefi/hl_universe.json`): a stale list beats an empty one, because
an empty one reads as "Hyperliquid has no markets", which is never true.

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
| `GET /hyperliquid/assets` · `GET /hyperliquid/stats` · `POST /hyperliquid/add` · `POST /hyperliquid/seed` | browse and list any HL pair, perp or spot |
| `POST /position/open` · `POST /position/close` · `GET /positions/{addr}` | trading |
| `POST /predict` · `GET /predictions[/{addr}]` · `/predictions/board` · `POST /predictions/resolve` | price calls |
| `GET /scoring` · `/scoring/models` · `/scoring/preview` · `POST /scoring` | the score, and its knobs |
| `GET /balance/{addr}` | PREFI minted / burned / locked / available |
| `POST /stake/lock` · `/stake/extend` · `/stake/unlock` · `GET /stakes/{addr}` | staketime |
| `GET /treasury` · `/treasury/history` · `POST /treasury/distribute` · `/treasury/claim` | epochs |
| `GET /leaderboard` · `GET /portfolio/{addr}` · `GET /prices` | read models |
| `GET /pool` · `/pool/config` · `POST /pool/config` | pool status and the owner's knobs |
| `GET /pool/vault` · `/pool/tokens` · `POST /pool/vault/create` · `/pool/vault/set` | the deposit address |
| `POST /pool/deposit?tx=` · `POST /pool/sync` · `GET /pool/balance/{addr}` · `/pool/ledger` | money in |
| `GET /pool/sign` · `POST /pool/stake` | the message to sign, and the stake |
| `POST /pool/free` · `GET /pool/free/{addr}` · `/pool/free/leaderboard` | a call with no money down, the quota, the board |
| `GET /pool/round` · `/pool/rounds` · `/pool/entries` · `/pool/leaderboard` · `POST /pool/settle` | pots |
| `POST /pool/withdraw` · `GET /pool/withdrawals` · `POST /pool/withdrawals/{id}/pay` | money out |
| `GET /hyperevm` | RPC reachability and chain id |

Tests, from `src/`:

```bash
python3 -m pytest tests/ -q                        # 208 cases, hermetic (no network)
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
  server-side only. On the **PREFI side** nothing is signed: an address is an
  identity, not a proof, so anyone who can reach the API can act as any address.
  The **pool** is the exception and had to be — staking and withdrawing carry a
  wallet signature over a nonce, and owner actions need the owner secret or the
  owner's signature.
- The pool is **custodial**. Deposits sit in one wallet; if it is the generated
  hot wallet, this host's filesystem is the security model. Solvency is reported
  (`GET /pool/vault`), not enforced by a contract.
- **HL prices need a reachable feed.** If the local `hyperliquid` module is down
  *and* the public endpoint is rate-limiting this IP, HL-sourced markets have no
  price: `add_hl_market` says so, and a due prediction stays open rather than
  settling against a wrong number. The pair browser is the one part that keeps
  working — it falls back to the last cached universe and `GET
  /hyperliquid/stats` reports how old it is.
- Resolution is lazy — a prediction settles when someone next reads it, or when
  `m prefi/resolve_predictions` runs. It scores the price *at* the resolve time
  either way, so lateness costs nothing; run it on a cron if you want the ledger
  to keep itself current.
