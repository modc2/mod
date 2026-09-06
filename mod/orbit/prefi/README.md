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

**Three ways to answer the same question.** Every entry is a price call scored
by the same rule; what differs is what backs it, and each is paid from a
different place so none can dilute another:

| | backed by | enters the pot | paid from |
|---|---|---|---|
| **stake** | dollars at risk | yes | the pot, by `dollars × accuracy` |
| **agent** | bloctime locked | no | the protocol's fee, by `usd_seconds × accuracy` |
| **free** | nothing | no | nothing — it is scored and ranked only |

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

### Agent calls — stake time instead of dollars

An agent puts no money down either, but it is not playing for free. It plays
for **locked time**, and it gets paid in real dollars.

The qualification is [bloctime](../bloctime), the time-weighted staking
contract on Base Sepolia. An agent's weight in a round is exactly the quantity
bloctime mints BLOC for, scoped to that round:

```
usd_seconds = Σ  (USD value of a lock) × (seconds its lock window overlaps the round)
```

Lock $100 for the whole of a weekly round and you carry 60,480,000 usd·seconds.
Lock the same $100 for the last day of it and you carry 8,640,000. Lock nothing
and you cannot call at all. Nothing about the agent's own balance enters this —
only what it committed, and for how long.

```bash
m prefi/agent-stake address=0x… asset=BTC price=80000   # no dollars, time down
m prefi/agent-quota address=0x…                         # calls left + live weight
m prefi/agent-board                                     # agents by what they earned
```

**What agents split is the protocol's own profit, never a staker's pot.** When
a pot settles, `agent_share_bps` of its protocol fee (50% by default) becomes
an agent pot for that asset, divided by `usd_seconds × accuracy` and credited
to the ledger as real, withdrawable dollars. The rest of the fee goes to the
treasury as it always did. A test asserts the conservation directly: for every
settled pot, `agent_paid + treasury == fee`, to the micro-dollar. An agent
therefore cannot cost a staker a cent — the money it wins is money the pool
had already taken off the top.

Three consequences worth stating, because they are the design and not
accidents of it:

- **A pool with no fee pays agents nothing.** The agent pot is a slice of the
  fee, so at `fee_bps=0` calls are scored, ranked and settled but pay $0. The
  UI says so where an agent would otherwise read "50% of the fee" and expect
  money. Set a fee to fund it.
- **Weight is recomputed at settlement and the larger of the two wins.** The
  weight snapshotted at entry is a floor, so locking *more* later in a round
  still counts, and a lock that expires mid-round keeps the seconds it served.
- **An unreachable bloctime feed is never read as zero.** `agent_weight`
  returns `None` rather than `0.0`, entry refuses with "cannot verify", and
  settlement falls back to the snapshot. A dead feed must not silently
  disqualify everyone who locked.

The same two caps as free play — one call per asset, `agent_per_round` (10)
overall — and the same signature, because a call that claims a share of real
fee money has to prove it owns the address claiming it. `m prefi/pool-set
agent_per_round=0` switches agent play off; `agent_share_bps=0` keeps the
board but sends the whole fee to the treasury.

Agents read their weight through the local bloctime module (`PREFI_BLOCTIME_API`,
default `:8851`), falling back to the activator at `:9000/api/bloctime` — that
module is scale-to-zero, so a refused direct call means asleep, not absent.

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
m prefi/pool-set interval=604800 fee_bps=100 min_liquidity_usd=10000 secret=…
m prefi/deposit tx=0x…              # credit a deposit
m prefi/round                       # the pot, with live provisional scores
m prefi/free-stake address=0x… asset=BTC price=80000   # no deposit needed
m prefi/agent-stake address=0x… asset=BTC price=80000  # backed by bloctime, not dollars
m prefi/settle                      # settle closed rounds (safe on a cron)
```

## Scoring

A call is scored on its **normalized dollar error** — the miss in dollars over
the price it was predicting:

```
e       = |predicted − actual| / actual         # 0.01 == 1% off
score   = f(e)                                  # 0..1 — a score FUNCTION
payout  = burn × multiplier × score             # predictions: freshly minted PREFI
payout  = pot × (dollars × score) / Σ           # the pool: pro-rata by dollars × score
```

Normalizing is what makes the score comparable across assets: being $640 off on
BTC at $64,000 and $0.004 off on AERO at $0.41 are both a 1% miss and score
identically.

### The score function is a program

`f` is not a fixed menu. It is a **score function**: a one-line expression
over `e` plus a dict of named parameters, written in a small sandboxed
language (`src/curves.py`). The defaults are written in it, and so is anything
you write yourself:

| name | expression | params | shape |
|---|---|---|---|
| `linear` *(pool default)* | `max(0, 1 - e/tol)` | `tol` | straight ramp; `tol=1` is exactly `1 − e` |
| `l2` *(predict default)* | `1 / (1 + (e/tol)**2)` | `tol` | inverse-square; never quite zero |
| `exponential` | `exp(-e/tol)` | `tol` | 1/e at `tol`, punishes the tail |
| `threshold` | `1 if e <= tol else 0` | `tol` | all or nothing |
| `gaussian` | `exp(-(e/tol)**2)` | `tol` | flat shoulder, then falls fast |
| `tiered` | `1 if e <= tol else (0.5 if e <= 2*tol else (0.25 if e <= 4*tol else 0))` | `tol` | a ladder |
| `cushion` | `max(base, 1 - e/tol)` | `tol`, `base` | ramp with a floor — nobody is zeroed |
| `hinge` | `max(0, 1 - (e/tol)**power)` | `tol`, `power` | flat then a cliff; `power` sets the corner |

The language: the variable `e` (alias `err`), your parameters by name, numbers,
`+ - * / ** % //`, comparisons, `and/or/not`, `x if cond else y`, and
`abs min max exp log sqrt pow tanh floor ceil round clamp(x,lo,hi) where(c,a,b) sign`.
Nothing else — no attribute access, subscripts, imports or undeclared names; it
is walked as an AST and evaluated by `curves.py`, never `eval`'d. Output is
clamped to `0..1`; a division by zero or an overflow scores 0 instead of
stalling a settlement. `GET /functions/language` is the cheat sheet.

The pool's `tolerance` knob sets the function's `tol` parameter; `model_params`
overrides the rest by name (`pool-set model=hinge tolerance=0.05
model_params='{"power":4}'`).

### Write, try, save, share

```bash
m prefi/functions                                          # defaults + library, curves sampled
m prefi/fn-test expr='max(base, 1 - e/tol)' params='{"tol":0.05,"base":0.1}'
                                                           # validate, draw, settle a mock $500 pot
m prefi/fn-save address=0x… name=soft expr='…' params='…' description='…'
                                                           # signed like a free call (fn-sign shows the text)
m prefi/pool-set model=soft tolerance=0.05 secret=…        # the owner switches the pot to it
m prefi/set-scoring model=soft tolerance=0.02              # …or the predict layer
m prefi/fn-share name=soft                                 # a share code: prefi.fn.<base64>
m prefi/fn-publish name=soft [token=…]                     # → a core/store CID (your protocol token)
m prefi/fn-import source=<code-or-CID> [address=0x… name=…] # preview; with address, save (signed)
```

HTTP: `GET /functions`, `/functions/{name}`, `/functions/language`,
`POST /functions/test`, `/functions/sign`, `/functions` (save),
`DELETE /functions/{name}`, `GET /functions/{name}/share`,
`POST /functions/{name}/publish` (Bearer token forwarded to the store),
`POST /functions/import`. The console has a **Functions** tab for all of it.

A saved name belongs to the address that saved it — only that address can edit
or delete it; defaults can't be touched. An import keeps the original `author`
and records the `origin_cid`. Publishing uses the *caller's* mod-protocol token,
so the store's whitelist, quota and terms apply to them, not to this module.

### What is frozen when

A round **snapshots the whole program** — `{name, expr, params}` — the moment
it opens, and a prediction snapshots it when it is placed. Editing, retuning or
deleting a function later cannot re-price a bet already on the table; a
deleted function's old rounds still settle under exactly the rule they were
sold with. Rounds from before functions existed carry a built-in name and a
tolerance, which is enough to rebuild the same program.

Predict-layer params live in `~/.mod/prefi/scoring.json` (`model`, `tolerance`,
`model_params`, `multiplier`, `horizon`, `min_burn`, …); the library is
`~/.mod/prefi/functions.json`.

```bash
m prefi/set_scoring model=exponential tolerance=0.01 multiplier=5
m prefi/score_preview predicted=64640 actual=64000   # score a hypothetical
```

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
- **`bittensor`** — **every Bittensor subnet's alpha token**, priced in TAO,
  read through the local [`bt`](../bt) module. `add_bt_market` verifies the
  subnet is in the indexer before listing it. See [Subnets](#subnets).
- **`dex`** — **any token with a pool on Solana or Base**, priced per pool by
  DexScreener, settled on GeckoTerminal's hourly candle for that pool, and
  gated by the pool owner's liquidity floor. See
  [Solana and Base tokens](#solana-and-base-tokens).

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

### Subnets

Every Bittensor subnet has an alpha token whose price the chain sets in TAO,
and the `bt` module's indexer snapshots all ~130 of them every five minutes
into SQLite. That is what makes a subnet a listable market here: a mark now
(`bt_prices_at` with no `ts`) and a mark *at the close* (`bt_prices_at` with
one) both come from the same feed, so a pot settles against the price the
indexer actually recorded rather than whatever the chain says when someone
finally reads the round.

```bash
m prefi/bt_stats                      # 128 subnets · τ83k 24h volume · 3 listed
m prefi/bt_assets search=chutes       # by name, netuid, SN64 or the alpha glyph
m prefi/add_bt_market subnet=64       # netuid
m prefi/add_bt_market subnet=lium.io  # or name
m prefi/seed_bt limit=20              # the 20 busiest by 24h alpha volume
```

A subnet market is `SN{netuid}` (`SN64`), carries `bt_netuid`, `bt_name` and
`quote: "TAO"`, and **prices in TAO** everywhere — the stake form, the pot, the
settlement. Stakes and payouts are still dollars; the quote unit only decides
what a "1% miss" is measured against. `GET /markets` adds a `price_usd` shadow
when the TAO/USD mid is available from Hyperliquid, for display only. Root
(netuid 0) is not listable: its price is 1 TAO by definition.

The `bt` module is read the same two-door way as `hyperliquid`
(`PREFI_BT_API`, default `http://localhost:50280`, then the activator at
`PREFI_BT_WAKE`, default `http://localhost:9000/api/bt`) and the subnet list is
cached 15 minutes in memory and on disk (`~/.mod/prefi/bt_universe.json`).
There is no public fallback — the module *is* the feed — so if it is down, a
listed subnet has no mark and a due pot stays open rather than settling
against nothing. The console has the same thing under
**Markets → + Bittensor**.

### Solana and Base tokens

Anything with a pool on Solana or Base is listable, which is a different shape
of universe from Hyperliquid's ~880 pairs or Bittensor's ~130 subnets: it is
unbounded, and most of it is worthless. Two things keep it honest.

A market is **one pool**, not a ticker. `add_dex_market` resolves what you typed
— a pool address, a token address, or a symbol — to the token's deepest pool on
that chain and records its address (`dex_pair`). Every later price and every
settlement reads that pool, so a token can never be quietly re-pointed at a
thinner one. The symbol carries the chain (`WIF.sol`, `BRETT.base`) because the
same ticker is on Hyperliquid, Solana and Base at once and the pot on each is a
different thing.

And the **pool owner sets a dollar floor on liquidity**: `min_liquidity_usd`
(default $10,000, `0` = no floor) in the pool config, next to the interval and
the fee, settable the same signed way. A token under it cannot be listed, and a
listed token whose pool has drained under it **cannot take a stake** until it
refills — the floor is checked against the live reading at stake time, not the
one from listing day. A pot on a $900 pool settles against a price one trade
can move, and the owner is the one whose depositors would pay for that.

```bash
m prefi/pool-set min_liquidity_usd=25000 secret=…   # the owner's floor
m prefi/dex-stats chain=solana        # 54 pools ranked · 18 clear the floor · 0 listed
m prefi/dex-assets chain=base search=BRETT
m prefi/add-sol address=EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm   # by mint
m prefi/add-base address=0x532f27101965dd16442E59d40670FaF5eBB142E4    # by token
m prefi/add-dex chain=solana address=WIF                              # by symbol
m prefi/seed-dex chain=base limit=20  # the 20 busiest that clear the floor
```

Spot and liquidity come from DexScreener (no key; one read per chain prices
every listed pool, cached 60s). Addresses resolve there too — a pool address
exactly, a token address to its deepest pool. **Symbol searches go to
GeckoTerminal**, whose search puts the real `$WIF` first; DexScreener's
returns thirty pump.fun namesakes and a "WIF" with $35k behind it, and a fake
pool can park tokens to fabricate liquidity but not volume — so a symbol takes
the *busiest* exact-ticker match, and the browser sorts exact matches first,
then by 24h volume. The mark at a round's close comes from
GeckoTerminal's hourly OHLCV for the same pool — the candle opening nearest the
close, its open *is* the price at that boundary. If GeckoTerminal can't answer,
the API's own snapshots stand in: it records a point per listed token every
five minutes (`~/.mod/prefi/dex_history.json`, 30 days, `POST /dex/snapshot`
to force one, `PREFI_DEX_SNAPSHOTS=0` to stop the timer), and a point within
30 minutes of the close settles the pot. Only then does it fall back to spot,
inside the pool's usual `spot_grace`, and says so.

The console has the same thing under **Markets → + Solana / + Base**: the
chain's busiest pools by default, a search on top, liquidity and
24h volume per row, and rows under the floor shown greyed with the number
rather than hidden — the owner's floor is printed on the panel so nobody has
to find it out on click.

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
| `GET /bittensor/assets` · `GET /bittensor/stats` · `POST /bittensor/add` · `POST /bittensor/seed` | browse and list any Bittensor subnet, priced in TAO |
| `GET /dex/assets?chain=` · `GET /dex/stats` · `POST /dex/add?chain=&address=` · `POST /dex/seed` · `POST /dex/snapshot` | browse and list Solana/Base tokens over the owner's liquidity floor |
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
| `POST /pool/agent` · `GET /pool/agent/{addr}` · `/pool/agent/leaderboard` | a call backed by bloctime, the quota + live weight, the board |
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
