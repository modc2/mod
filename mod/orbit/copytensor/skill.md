---
name: copytensor
description: Bittensor dTAO copy trading — mirror subnet allocations of top performers. Reads (subnets, positions, trader history, PnL) are served by the bt module's local index, with a rotating pool of public RPC endpoints as fallback. No third-party APIs, no wallet required to browse.
type: orbit-module
---

# copytensor

Mirror top Bittensor validators' dTAO subnet allocations. All read paths
(leaderboard, subnets, account, trader, PnL) work without a wallet. They are
served by the **`bt` module's local index** — the same SQLite indexer behind
the bt explorer, which snapshots subnet pools and tracked coldkeys on an
interval — and fall back to a round-robin pool of public Bittensor RPC
endpoints whenever bt is stopped. Only stake/unstake operations need a
wallet, and those always go out over our own wallet + RPC pool.

## Capabilities

- **Public reads, no wallet needed**: leaderboard, subnets, account positions, trader profile, PnL.
- **bt-backed reads**: `src/chain/bt_source.py` wraps bt's `POST /api/call` in a `SubtensorClient` (`BtBackedClient`), so every existing read path — endpoints, snapshot loop, PnL, the copy engine's target lookup — is served from bt's index without changing its call sites. `/subnets` drops from a multi-second `all_subnets()` walk to ~150 ms. Each override falls back to the inherited RPC implementation if bt can't answer.
- **Trader tracking**: watching a coldkey registers it with bt's trader index (`bt_track`), which then keeps its equity curve, windowed PnL and an inferred trade tape. Read them via `/traders`, `/traders/{ss58}`, `/traders/{ss58}/history`, `/traders/{ss58}/flows`, `/flows`.
- **PnL curve with trades on it**: `/account/{ss58}/curve` replays the local snapshot record into an equity series (one point per snapshot = a real block) and infers each trade from per-subnet alpha deltas. Every step splits exactly into `Δvalue = market move + stake flow`, so the response carries both `market_pnl_tao` (what the book did) and `flow_tao` (what the trader deposited) next to net PnL — a +282% headline that is 98% deposits reads as such. Trades come back as individual legs *and* grouped events, each carrying the curve value at its timestamp so the chart pins markers onto the line. The trader page (`src/app/app/components/PnlCurve.tsx`) renders it with PNL/VALUE/MARKET modes, ▲/▼ markers sized by TAO moved, and a trade tape cross-highlighted with the chart.
- **Market surface**: `/subnets` passes bt's screener through whole — symbol, market cap, 24h volume, 1h/24h change, a 48-point price sparkline and the on-chain subnet identity (logo, description, github/site/discord) — behind a 12 s cache, since the ticker, the grid and the header strip all poll it. `/market` adds network totals and the day's movers; `/subnets/{netuid}` adds validator rankings; `/subnets/{netuid}/history` is the detail chart's series. Enriched fields are `null` (never 0) when bt is down and the read falls back to the RPC walk, and the UI renders those as "—". The subnet grid has card and table views (`copytensor:subnets:view`), sorts by mcap / volume / change / price / pool / netuid, and links each row to the detail page.
- **Round-robin RPC pool**: `entrypoint-finney.opentensor.ai`, `archive.chain.opentensor.ai`, `lite.chain.opentensor.ai`, `bittensor-finney.api.onfinality.io` — shuffles on init, auto-fails over on RPC errors.
- **Copy engine**: replicate a target validator's subnet allocations onto your own hotkey with safety limits (per-tx cap, daily cap, rebalance threshold).
- **Picking traders off the board** (v0.6.1): every row on `/traders` has a tick box and the whole row opens the profile — the name used to be the only hit target in a 900px row. Ticking anything raises a bar with `ALL <n>` / `CLEAR` / `+ COPY n SELECTED`, which seeds the strat maker's basket in one go; the header box selects everything the filters are showing (indeterminate when it's a subset), and a ticked row keeps the accent gutter hover paints. The `history` rail (1/3/7/14/30d, default **7d**) is the window every PnL column is measured over, lives in `FiltersContext` (persisted to `ct_filters_v1`) and is now shared with the trader profile, so clicking a row off a 7d board lands on a 7d profile. Selection survives opening, popping out and closing the drawer: `SidebarShell` renders `children` from one slot in all three housings, where it used to return them bare when shut and remount the whole page on a dock toggle.
- **Index of traders (polymarket-style)**: build a named, weighted basket of validators and "Start Index Live" — the frontend spawns one server-side copy per trader with capital split by weight. Pause / Resume / Sync / Stop act on the whole basket.
- **Strats live on the server, owned and shareable** (v0.8.0): a strat is a row in `strats` (SQLite), owned by the SHA-256 of the caller's `X-Owner-Key` — a random key each browser mints once (`src/app/app/lib/owner.ts`) and never sends anywhere else. There is no sign-up: the first 16 hex of that hash is your **fingerprint**, the id you hand someone who should read a private strat of yours. Visibility is `private` (the default), `whitelist` (you plus the fingerprints you list) or `public` (the HUB tab on `/strats`, where anyone can read it and CLONE it onto their own key, private and never live). Writes are owner-only; a non-owner gets 403 on write and 404 on anything they may not read. Anything still in the old localStorage list (`copytensor:indexes:v1`) is migrated up on first load and then deleted.
- **Backtest on every edit** (v0.8.0): `POST /strats/backtest` replays the basket inline — no id, so an unsaved draft backtests too — and the picker re-runs it (debounced 500 ms) on every membership, weight, on/off, window or capital change. It's a *return* replay: each trader's indexed portfolio series becomes a return series, blended at the basket's normalized weights and rebalanced every step, which is what the copy engine's rebalance threshold approximates. What it does NOT model is execution lag, slippage and fees — a copied trade lands after the leader's, so live sits under the curve, and the panel says so. Legs with no indexed history are reported under `skipped` (their weight is redistributed, never counted as flat), a book under `DUST_TAO` (0.5τ) can't contribute a step (a wallet going 0.000001τ → 1τ is not a 100,000,000% return), a basket wider than `MAX_LEGS` (60) is truncated to the heaviest legs **out loud**, and per-trader contributions are attributed in money against the equity going into each step, so they sum exactly to the basket's PnL. `tests/test_backtest.py` pins all of it on fixtures.
- **Our hotkey is prefilled, not asked for**: the copy engine stakes from *our* account, so a live run needs `our_hotkey` — but the module already knows it once a wallet is set, so the field fills itself from `/wallet/balance`. Saving and backtesting need nothing; only START asks, and when it's empty it says "no wallet set" instead of "our hotkey ss58 required".
- **A strat agent you can talk to** (v0.7.0): the AGENT tab is a conversation with a Claude wired to this module's own boards, and its deliverable is a basket. See "The strat agent" below.
- **The leaderboard is bt's** (v0.4.0): one call to bt's `bt_trader_board` ranks every coldkey bt indexes over the window, PnL already split into market move vs stake flow — **~260 ms for the whole board** against 211 s for the old per-account archive walk (measured on a 253-account pool). `src/engine/bt_board.py` only maps bt's rows onto the entry shape the API already served, so `/leaderboard` and the UI are unchanged. All five horizons warm in ~5 s at boot instead of ~7 min, and a cold horizon is priced **on the request thread** rather than returning `[]`. `build_leaderboard`'s chain walk is still there and takes over automatically when bt is down; `/universe` reports which engine priced each horizon (`board.source`) and how many coldkeys bt indexes (`board.indexed`). What this trades away: the board ranks what **bt indexes**, not the whole watchlist — see the mirror below.
- **Mirroring the watchlist into bt**: bt only keeps history for coldkeys it tracks, so `bt_mirror_max` (120) is the real size of the visible board. Each tracked account costs bt one chain read per refresh pass (~1.75 s measured), so 120 accounts is ~3.5 min of every 15-min pass — raise it knowing that cost. The mirror pushes only accounts bt does not already have: `bt_track` snapshots on every call, so re-mirroring the same list each restart would spend minutes of chain reads to learn nothing.
- **Trader pool (what the watchlist holds)**: the watchlist feeds the mirror, and with bt down it IS the ranked set. One `get_delegates()` walk (~35 s, cached 6 h) yields the whole on-chain universe — every delegate owner **and every nominator staking to them**: ~2.3 k + ~57 k real coldkeys on finney — ranked by stake. At boot the pool tops itself up to `leaderboard_pool_size` (250; `auto_discover: false` disables it); `POST /pool?size=N` resizes it live (background, poll `GET /universe`), `POST /discover?top=N&kind=validator|nominator|all` adds the top N synchronously. Ranking stake is `Σ` per-subnet alpha from the delegate set — a ranking heuristic for *which* coldkeys to watch, never shown as a τ value; every τ figure on the board comes from priced positions. Every ss58 entering the watchlist is checksum-validated.
- **Honest PnL**: baselines come from local snapshots (30-min loop) or bt's trader index (`bt_trader_at`), which only counts a snapshot as a baseline if it actually sits near the block asked for — otherwise today's book would masquerade as last week's and PnL would read 0. The archive-node query is the fallback behind both (`archive_fallback: true`, `COPYTENSOR_ARCHIVE_FALLBACK=0/1` overrides): a pool of hundreds is only comparable if every trader is priced over the *same* window, and for a coldkey nobody has indexed only the archive can answer. Each row reports `window_days` — the history it actually covers — and the UI flags any row short of the horizon; if no baseline exists at all the row reports `baseline: false` and PnL 0 ("— warming"). Numbers are never invented.
- **Scaling the fallback board** (bt down only): one build = one live read + one archive read per trader, so the pool is walked concurrently (`leaderboard_workers`, 8) over a pool of archive sockets (`archive_pool_size`, 4), with live positions cached briefly so all five horizons share one read per trader. That path stays off the request thread — a cold horizon returns `[]` with `board.building` set in `/universe` and rows appear on the next poll — and it stands aside while the delegate walk runs. It also runs alone, behind `_lb_build_gate`; the bt build deliberately sits *outside* that gate, because a walk that started while bt was down otherwise held its horizon empty for minutes after bt came back (`_leaderboard_cached` prices a cold horizon off bt even while a walk is marked refreshing). Refresh is rate-limited to one rebuild per three build-times so a big pool can't rebuild forever. SQLite runs in WAL (concurrent snapshot writers).
- **Process supervision**: both services run under pm2 (`copytensor-api`, `copytensor-app`) and are in the pm2 dump, so a reboot restores them. `_pm2_spawn` passes `--interpreter none`: pm2 hands anything it doesn't recognise to node, so the python API crash-looped on `SyntaxError: Cannot use import statement outside a module` while `pm2 start` still exited 0 — the module reported "started", nothing was listening, and after a reboot the app served 500s against a dead backend for hours. `pm2 start` exiting 0 means the process was *accepted*, so the spawn now also waits for it to be online with a still restart counter before claiming success, and falls back to Popen otherwise.

## Usage

### Python
```python
import mod as m
ct = m.mod("copytensor")()

ct.serve()                                    # docker by default, falls back to local
ct.subnets()                                  # list all subnets (public)
ct.leaderboard(days=7, top=50)                # top performers (public)
ct.account("5CWzmvA17MAM...")                 # coldkey allocations + PnL
ct.rpc_pool()                                 # which RPC is active + full pool
ct.source()                                   # reads served by bt or by RPC?
ct.traders()                                  # tracked traders: value, PnL, allocation
ct.trader_history("5CWzmvA17MAM...")          # equity curve from bt's index
ct.flows(hours=24)                            # inferred buys/sells across traders
ct.agent_status()                             # can the strat agent run?
r = ct.ask("5-trader index, books over 1000 TAO, 200 TAO capital")
r["strat"]["traders"]                         # the basket it picked, with a `why` each
ct.ask("drop the bottom two", session_id=r["session_id"])   # keep talking
ct.set_wallet(mnemonic="...")                 # only needed to actually stake
ct.create_copy(target_ss58="...", our_hotkey="...")
```

### CLI
```bash
m copytensor/serve
m copytensor/leaderboard days=7 top=20
m copytensor/account ss58=5CWzmvA17MAM...
m copytensor/rpc_pool
m copytensor/source
m copytensor/traders
m copytensor/flows hours=24
m copytensor/agent_status
m copytensor/ask question="4-trader momentum index, books over 5000 TAO" stream=True
m copytensor/create_copy target_ss58=... our_hotkey=...
```

## API surface

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | Active RPC + full pool |
| GET | `/status` | Block height, tracked accounts, active copies, `reads: bt\|rpc` |
| GET | `/subnets` | All subnets, enriched: price, 1h/24h change, market cap, 24h volume, 48-pt sparkline, symbol, logo, description, links |
| GET | `/market` | Network totals (alpha mcap, 24h volume, block, TAO/USD) + top gainers/losers |
| GET | `/subnets/{netuid}` | Pool state + on-chain identity + validator rankings |
| GET | `/subnets/{netuid}/history?hours=168` | Indexed price / mcap / volume series |
| GET | `/leaderboard?days=7&top=50` | Top performers by alpha PnL (`days=0` = all history) |
| GET | `/coverage` | How deep the index is: `depth_days`, `oldest_ts`, per-horizon `covered`/`pct` |
| GET | `/account/{ss58}?days=7` | Allocations + PnL |
| GET | `/account/{ss58}/pnl?days=7` | Detailed per-subnet PnL |
| GET | `/account/{ss58}/curve?days=7` | Equity/PnL curve from local snapshots + the trades on it |
| GET | `/trader/{ss58}` | Full profile |
| GET | `/trades?limit=50&copy_id=...` | Copy-engine trade history |
| GET | `/traders?sort_by=total_tao` | Tracked traders: value, allocation, windowed PnL (bt) |
| GET | `/traders/{ss58}?hours=168` | Indexed profile: positions, equity curve, inferred trades |
| GET | `/traders/{ss58}/history?hours=168` | Portfolio value over time |
| GET | `/traders/{ss58}/flows?hours=168` | One trader's inferred buys/sells |
| GET | `/flows?hours=168` | The tape across every tracked trader |
| POST | `/watch` | Add coldkey to watchlist (checksum-validated) |
| GET | `/watches` | List watched coldkeys |
| POST | `/discover?top=8&kind=validator` | Watch the top-N coldkeys on-chain (blocking, ~35 s cold) |
| GET | `/universe` | Pool status: traders ranked vs coldkeys on-chain, board build progress |
| POST | `/pool?size=250` | Resize the trader pool; grows + re-prices in the background |
| GET | `/tao_price` | TAO/USD (coingecko, 5-min server cache) |
| GET | `/agent` | Strat-agent status: auth method, model, tool names |
| POST | `/agent/ask` | Talk to the strat agent — SSE stream, `{question, session_id?}` |
| POST | `/copy` | Create copy config |
| GET | `/copies` | List active copies |
| POST | `/copy/{id}/{pause,resume,sync}` | Manage copy |
| POST | `/wallet/set` | Set mnemonic (only for staking) |
| GET | `/wallet/balance` | Wallet TAO balance |
| GET | `/whoami` | Your strat-owner fingerprint (from `X-Owner-Key`) |
| POST | `/strats/backtest` | Replay a basket inline: equity curve, stats, per-trader contribution, skipped legs |
| GET/POST | `/strats` | Your strats (+ public + whitelisted-to-you) / create one |
| GET | `/strats/hub` | The public shelf |
| GET/PUT/DELETE | `/strats/{id}` | Read / replace / delete (writes owner-only) |
| POST | `/strats/{id}/clone` | Copy a readable strat onto your key, private and flat |

## Structure

```
mod/orbit/copytensor/
├── config.json                  # fns list, endpoints, public RPC pool, seed validators
├── docker-compose.yml
├── Dockerfile
└── src/
    ├── mod.py                   # Mod orchestrator (Copytensor)
    ├── agent/
    │   ├── tools.py             # The strat agent's toolbox (trimmed reads + propose_strat)
    │   ├── mcp_server.py        # MCP dispatcher: stdio server + the POST /mcp mount in api/app.py
    │   └── agent.py             # Claude CLI driver: SSE events, session resume
    ├── api/
    │   ├── app.py               # FastAPI app
    │   └── models.py            # Pydantic response models
    ├── chain/
    │   ├── client.py            # SubtensorClient with round-robin RPC failover
    │   ├── bt_source.py         # BtSource + BtBackedClient — reads via the bt module
    │   └── snapshot.py          # Periodic snapshot capture
    ├── engine/
    │   ├── bt_board.py          # The leaderboard, ranked by bt's index (the default path)
    │   ├── leaderboard.py       # Fallback board: rank watched accounts by walking the chain
    │   ├── pnl.py               # Per-subnet PnL calc
    │   ├── curve.py             # Equity/PnL curve + trades inferred from snapshot deltas
    │   ├── copier.py            # Copy engine
    │   └── safety.py            # Safety limits
    ├── db.py                    # SQLite (snapshots, trades, copies, watches)
    └── app/                     # Next.js frontend (pixel theme, CRT shell)
```

## The strat agent

A conversation whose output is a basket. `/agent` in the console, `ct.ask()` from
Python, `m copytensor/ask` from a shell — all three drive the same run.

**The shape.** `src/agent/agent.py` runs the Claude CLI headless
(`claude -p --output-format stream-json`) with `src/agent/mcp_server.py` as its only
toolbox, and translates the CLI's stream into console events:
`start | text | tool | tool_done | strat | done | error`. `strat` is the event that
matters — it carries the validated basket, which the UI renders as a card.

**The toolbox reads this module's own API**, not the chain. `ct_traders` (the tracked
board, and the pool a basket is picked from), `ct_trader`, `ct_trader_flows`,
`ct_flows`, `ct_leaderboard`, `ct_subnets`, `ct_market`, `ct_copies`, `ct_status` —
each one a trimmed call against `localhost:50150`. Trimming is the point: a raw
`/traders` is 372 rows carrying sparklines and hotkey lists, which is a token bill and
no more signal than the dozen fields a strat decision actually turns on. The agent
therefore sees exactly the numbers the console shows, through the same cache, the same
bt index and the same RPC fallback.

**`propose_strat` is the deliverable**, and the only tool that isn't a read. It
validates SS58s, drops duplicates, normalises weights into `share_pct` and re-prices
every pick off the live board — so the card renders without a second round-trip, and
the agent gets told what it actually picked (a coldkey with no index history comes
back flagged `tracked: false` in `warning`).

**It cannot trade.** There is no write tool in the registry at all, so no prompt can
reach `/copy` or a wallet. A proposal lands in the console as a card; SAVE writes it
into the same localStorage strat library the strat maker uses (`copytensor:indexes:v1`,
with `thesis` alongside the weights), and OPEN IN STRAT MAKER saves then loads it into
the drawer's builder for the hotkey and the ACTIVATE click. Going live stays exactly
where it was.

**Talking, not asking.** Every event carries the CLI's `session_id`; sending it back
with the next question resumes that conversation (`--resume`), so "cut it to the best
two and call it Duo" costs 2 turns and no new research. The console keeps the
transcript and the session id in `copytensor:agent:v1` (capped at 60 rows — one origin
is shared by every module on this host).

**Auth** cascades `ANTHROPIC_API_KEY` → `~/.mod/copytensor/anthropic.key` (created
empty at 0600 if nothing exists) → Claude CLI OAuth. `GET /agent` reports which one
answered, and `m copytensor/test` fails the `agent` check when none do.

## MCP — sync the book from any client

The API **is** the MCP server: `POST /mcp` on the API port speaks streamable
HTTP (JSON-RPC 2.0, batches ok), and the gateway forwards it as
`/api/copytensor/mcp`. `GET /mcp/schema` (or `m copytensor/mcp`) lists the
transports and every tool before you connect.

```bash
claude mcp add --transport http copytensor http://localhost:50150/mcp
# through the gateway / from another box:
claude mcp add --transport http copytensor https://<host>/api/copytensor/mcp
# stdio, if you would rather not go over the network (same dispatcher):
claude mcp add copytensor -- python3 -m src.agent.mcp_server   # from the module dir
```

25 tools in two scopes (`src/agent/tools.py`):

| scope | tools | who sees it |
|---|---|---|
| `agent` (read-only) | `ct_status ct_market ct_subnets ct_traders ct_trader ct_trader_flows ct_flows ct_leaderboard ct_copies propose_strat` | the strat agent — `agent.py` launches the stdio server with `COPYTENSOR_MCP_SCOPE=agent`, so it can't even list the ops set |
| `ops` | `ct_wallet ct_copy ct_create_copy ct_resize_copy ct_pause_copy ct_resume_copy ct_delete_copy ct_portfolio ct_sync ct_trades ct_watch ct_unwatch ct_watches ct_strats ct_backtest` | any MCP client on `/mcp` (default scope `all` = both) |

The sync flow an MCP client runs:

1. `ct_wallet` — is a signer loaded? (Loading one is `POST /wallet/set` /
   `m copytensor/set_wallet`; a mnemonic is never an MCP argument.)
2. `ct_create_copy {target_ss58, alloc_tao}` — `our_hotkey` defaults to the
   loaded wallet. Or `ct_resize_copy` / `ct_pause_copy` on an existing one.
3. `ct_sync {dry_run: true}` — the plan, unsigned. Same object the live pass
   executes, so the preview cannot drift.
4. `ct_sync` — signs the stake/unstake extrinsics that close the gap. Always the
   **whole** portfolio (sleeves only add up diffed together); a `copy_id` just
   names the reason and routes via `POST /copy/{id}/sync`. Long timeout
   (`COPYTENSOR_MCP_SYNC_TIMEOUT`, 600 s) — a wide book signs one tx per subnet.
5. `ct_trades` — what landed.

Every tool is a loopback call to a REST route above, so REST, `m copytensor/*`
and MCP can never disagree. That loopback is also why the `/mcp` handler
dispatches in the threadpool (`run_in_threadpool`): a blocking `requests` call
to ourselves from the event loop would wait on the loop it is blocking.
`ct_strats` reads private strats only with the browser's owner key
(`owner_key` arg or `COPYTENSOR_OWNER_KEY`). Pinned by `tests/test_mcp.py`
(fake API, no chain, no bt).

## UI — the 8-bit console

The whole look is a design system, not a set of per-component styles. It lives in
exactly two files, so the theme can be reasoned about (and changed) in one place:

- `src/app/app/globals.css` — tokens, primitives (`.pixel-panel`, `.pixel-btn`,
  `.pixel-input`, `.pixel-bar`, `.pixel-table`, `.pixel-badge`, `.page-head`,
  `.stat-tile`, `.arcade-prose`), the CRT layers, and the sprite decorations. The
  rules it enforces are written at the top of the file.
- `src/app/tailwind.config.js` — the three pixel faces, the five-hue arcade palette,
  and `borderRadius` pinned to `0` for **every** key including `full`, which is what
  squares off the `rounded-full` pills already in the component tree.

Five rules, in priority order: nothing is round; every edge is a solid 2–3px border
(depth is one hard offset rectangle, never a blur); pressing a control translates it
by its shadow offset; motion uses `steps()`; and the palette is five colours, with
lime/red reserved for P&L sign so colour always means one thing.

Two things worth knowing before editing:

- **The accent remap.** Components reach for stock Tailwind accents
  (`text-green-400`, `border-red-400`, `bg-amber-400/10`). Those classes are remapped
  onto the arcade palette at the bottom of `globals.css` rather than being rewritten
  across twenty files — the rules sit after `@tailwind utilities`, so they win the
  cascade at equal specificity. Recolour there, not in JSX.
- **Type sizing.** Press Start 2P (`.font-display`, `.arcade-title`) is drawn on an
  8×8 pixel em, so it only renders cleanly at **multiples of 8** — anything else
  resamples every glyph and the result looks smeared rather than pixelated. The
  heading scale is therefore 24/16/16px, set on `h1/h2/h3.font-display` in CSS
  (overriding the `text-2xl`/`text-lg` classes in the pages), and page titles run
  16px on a phone / 24px / 32px at `md`. The sprite shadow on `.arcade-title` is
  always exactly one design pixel — `font-size / 8`, so 2px at 16, 3px at 24, 4px at
  32. VT323 (`.font-mono`, every number and address) sets small for its point size —
  table cells run 17px and stat readouts 40px (30px on a phone).
- **Silkscreen is chrome only.** It has no real lowercase (the glyphs are
  small-caps-shaped), so a sentence set in it reads as one long shouted block with no
  word silhouettes left to scan. Labels, buttons and table headers: Silkscreen.
  Anything sentence-shaped — page standfirsts, subnet blurbs, empty states — goes in
  `.arcade-prose` (VT323 at 17px, capped at 72ch; `.arcade-prose-sm` for card-sized
  copy). Put the class on a `<p>`, not on a panel, or the 72ch measure caps the panel.

Two shared components carry the page furniture, and both replaced per-page copies
that had already drifted apart: `PageHeader.tsx` (title, one quiet VT323 line under
it, optional controls on the right — it used to be a boxed marquee band that ate a
third of the first screen) and `StatTile.tsx` (a scoreboard readout; `tone` paints
the lit strip across its top and the value). Use them rather than hand-rolling
another header or tile.

### The front door

`/` is a plain-language landing page (`components/Home.tsx`), built for someone who
has never seen a coldkey: a one-line pitch, the top nine traders of the last 7 days
as **cards** (rank, name, one big return %, "holds N τ · M subnets", COPY / DETAILS),
the copies you already run, and a three-step "how it works". Cards are filtered
harder than the board — priced, ≥25 τ held, ≥2 subnets, market % < 500 — because a
wallet that emptied itself over the window reads as +150 % market on −100 % total
and used to top the raw ranking. (The board itself now hides dust books < 1 τ too,
unless you're searching for one.)

COPY on a card opens `SimpleCopy.tsx`: one dialog, one number. If no wallet is set
it asks for the recovery phrase first (`POST /wallet/set`), then amount → START
(`POST /copy` with `alloc_tao`; hotkey, per-tx cap and rebalance band take the
server defaults). Every dial the dialog hides is still on the strat maker in the
drawer. `/portfolio` is now MY COPIES — `MyCopies.tsx` cards with PAUSE / SYNC /
STOP over the activity tape; the blended book with per-subnet drift stays on
`/strats` (`AllocationBook`).

The top bar is one row on desktop: logo, four doors — HOME, TRADERS, SUBNETS,
MY COPIES — search, the τ/$ toggle, and MORE. MORE is a drop holding everything a
first-timer doesn't need: STRATS, AGENT, the two drawer caps (WATCHLIST, STRAT
MAKER), the skin picker and the RPC readout. Under `lg` the rail scrolls under the
logo and MORE's contents become the `☰` sheet. `/leaderboard` still redirects to
`/traders`.

**The window is a first-class control, and it says how deep the index is
(v0.11.0).** `WindowRail.tsx` is the one horizon picker — 24H · 3D · 7D · 14D ·
30D · ALL — rendered on the front page, the board and the trader profile, all
driven by the single `days` in `FiltersContext`. `days = 0` is the all-history
window: `/leaderboard`, `/account/*` and `/trader/*` all map it through
`_win()` onto the server's `ALL_DAYS` (365) horizon, so the cache key stays put
while the real depth grows a day a day. `GET /coverage` derives, from one deep
board's per-row `window_days`, how far back the index actually reaches
(`depth_days`, `oldest_ts`) and how many traders clear each offered horizon;
`lib/useCoverage.ts` fetches it once per tab and every rail labels itself with
it — ALL carries the depth, a horizon only part of the index can answer is
marked amber with its percentage, and one that nothing can answer is disabled.
This existed because the console happily offered 30 days over a 12-day index
and the rows came back silently measured over less. `BoardFilters.tsx` adds the
other two knobs the front page used to hide in a constant: RANK BY
(return / total % / size / spread) and the τ book floor (`minStake`, default
25 τ), both shared with the board through the same context.

Charts are plotted on the pixel lattice, not drawn: `Sparkline.tsx` snaps vertices to
a 2px grid and emits an axis-aligned staircase, and the Recharts areas use
`type="stepAfter"` to match. A smooth interpolation is the fastest way to break the
spell, so if a new chart lands, step it.

### Handheld

The console is the same cabinet on a phone, not a cut-down one. The rules are in the
`── Handheld ──` block of `globals.css`; the one that matters most:

- **Nothing may be wider than the glass.** A single 500px control rail in the header
  used to push the layout viewport out to 659px, and the phone answered by zooming
  the *whole board* to 59% — every number on screen rendered at half its designed
  size. If the app ever "looks tiny" again, measure
  `document.documentElement.scrollWidth` against `innerWidth` before touching a font
  size; a page that overflows by one element shrinks everything.
- **Chrome shrinks, content doesn't.** Under 768px the marquee, tiles and standfirst
  step down a size; the numbers stay put. `PageHeader`'s standfirst folds behind a
  `?` cap so it can't push the data below the fold on every visit.
- **Rows of pills become rails.** `.rail` (a scroll strip that bleeds to the board
  edge) replaces wrapping — window pickers, sort keys, chart modes, nav tabs.
- **The two nine-column tables that ARE the product get a handheld layout, not a
  scrollbar.** The leaderboard (and the portfolio tape, and the copies list) render
  `.row-card` stacks under `lg` and the `<table>` above it. Detail tables keep the
  table and scroll sideways — but note `.pixel-table` is `table-layout: fixed`, so a
  `minWidth` has to be wide enough for the widest cell in *every* column or the name
  column collapses to an ellipsis.
- **Fingers get 40px.** `@media (pointer: coarse) and (max-width: 1023px)` floors
  every `.pixel-btn`; the width clause keeps touch-capable desktops out of it.

### The drawer, docked and popped out

The right-hand drawer (`SidebarShell.tsx` + `context/SidebarContext.tsx`) holds the
two things you do with a trader — `WatchlistDrawer` (keep an eye on it) and
`StratPicker` (put it in a basket) — and has three housings. Docked, it's a column
bolted to the right edge with a drag gutter (`.drawer-grip`) between it and the
board. **POP OUT** tears it off into a floating window (`.drawer-win`): drag it by
the title bar, resize from any of the eight edges, **ROLL** it up into its bar,
**MAX** it to the screen, **DOCK** it back. Position and size persist
(`ct_sidebar_mode`, `ct_sidebar_rect`) and are clamped back on screen on every load
and viewport resize; dragging within 20px of an edge snaps flush to it. Under 1024px
it is neither: `.drawer-sheet` covers the board, the page behind it is scroll-locked
and Escape closes it — a 420px column bolted to a 390px screen leaves nothing of the
board it exists to pick things off.

Two things that are load-bearing:

- **Dock and float are the same `<aside>`**, restyled — not two components. Moving it
  in the tree would remount both panels, so every pop-out would throw away a
  half-filled basket. For the same reason both panels stay mounted and the inactive
  one is `hidden`, rather than being swapped in and out.
- **The window's title bar is a separate rail from the tabs.** They shared one row
  first, which left about a caption's width of bar to grab at 420px and made every
  near-miss hit a button. Drag is suppressed over anything inside a `<button>`.

`compact` (the builder's one-column layout) keys on the drawer's *actual* width in
either housing, not on the EXPAND state.

### Skins

There are ten, and every one is a real cabinet rather than an inverted dark mode:
ARCADE (default), FLYER (the daytime flyer), MANUAL (instruction booklet), GAMEBOY
(DMG four-tone), PHOSPHOR (P1 green tube), AMBER (P3), C64, MIAMI, VECTOR, DINN
(Obra Dinn's 1-bit Macintosh). Each is
one `[data-theme="<id>"]` block in `globals.css` restating the *same* token list in
the same order — a block that drops a line silently inherits ARCADE's value for it,
which is the only way the system breaks. The five hues are declared as `r g b`
channels (`--neon-lime-rgb` …) and the named colours are derived from them once, so
tinted fills, glows and the horizon grid all follow a skin for free.

- `data-base` (`dark`/`light`) is stamped next to `data-theme` and carries the
  field classification. Every generic light-field rule — grille, glow suppression,
  input bevel, weight bump — keys on `data-base`, never on one id, so a new light
  skin needs no CSS beyond its tokens.
- Two invariants hold: lime = up / red = down (the monochrome tubes bend and keep one
  green and one burnt-red, because a P&L sign that needs a legend is broken), and the
  accent is never lime or red. DINN is the one exemption on the first — a two-colour
  screen has no second hue to spend, so gain and loss are both ink and the sign is
  carried by the ▲/▼, the +/−, and a dithered tile strip against a solid one.
- Registry is `context/ThemeContext.tsx` — id, label, base, and three chips sampled
  from the skin's own palette, which is what `SkinPicker.tsx` renders in the rail.
  Adding a skin = one entry there plus one token block.
- DINN also carries a texture block, and it's the LAST thing in `globals.css` on
  purpose: several of its rules override a `[data-base="light"]` rule at the same
  specificity, so source order is what settles them. Anything appended below it
  outranks the skin. It paints ordered dither (`--dither-06/12/25/50`, 4×4 SVG cells)
  wherever the board would otherwise fade something, and drains `.medal-plate` /
  `.subnet-logo` — the two surfaces whose colour is data, not a token — to greyscale.
- Charts read colours through `useThemeColors()`, not `var()`: Recharts and our SVGs
  paint into `stroke`/`fill` *attributes*, where a `var()` isn't dependable. The hook
  resolves the tokens off `<html>` and re-resolves on every skin change.

Check at least one light skin and one dark before shipping a visual change.
`prefers-reduced-motion` kills the CRT sweep and marquee.

## Env vars

| Name | Purpose |
|---|---|
| `COPYTENSOR_API_URL` | Override API URL (default `http://localhost:50150`) |
| `COPYTENSOR_BT_URL` | Where the bt module lives (default `http://localhost:50280`; `bt_url` in config.json also works) |
| `COPYTENSOR_BT` | `0` disables the bt path entirely — reads go straight to the RPC pool |
| `COPYTENSOR_ARCHIVE_FALLBACK` | `1` re-enables deep archive-node queries when bt has no history |
| `NEXT_PUBLIC_API_URL` | Frontend → API base (default same as above) |
| `COPYTENSOR_AGENT_MODEL` | Strat-agent model (default `sonnet`) |
| `COPYTENSOR_AGENT_MAX_TURNS` | Tool-use turns per question (default 16) |
| `COPYTENSOR_AGENT_TIMEOUT` | Hard kill on a run, seconds (default 300) |
| `COPYTENSOR_AGENT_BIN` | Claude CLI binary (default `claude`) |
| `ANTHROPIC_API_KEY` | Agent auth; else `~/.mod/copytensor/anthropic.key`, else Claude CLI OAuth |

## Mod protocol

- **Anchor**: `src/mod.py` class `Copytensor` (aliased as `Mod`)
- **Load**: `m.mod("copytensor")()`
- **Call any fn**: `m.fn("copytensor/leaderboard")(days=7)`
- **Default entry**: `forward()` returns module info; `forward(fn="leaderboard")` dispatches
- **Logs**: `/tmp/copytensor/api.log`, `/tmp/copytensor/app.log` (local mode), `docker logs copytensor` (docker mode)
- **Ports**: api 50150, app 3150
- **Gateway**: registered in `server.namespace.app_namespace` on first `serve()`. Accessible via the mod-protocol gateway on :3001 (`/copytensor` for app, `/api/copytensor/*` for API) and the caddy edge on :3000. Use `m.copytensor.gateway()` (or `m copytensor/gateway`) to print live URLs.
- **Docker**: `docker compose up -d --build` from the module dir, or `m copytensor/serve` (auto-picks docker when available, falls back to local with the prebuilt arm64 binary). Image: `copytensor-copytensor:latest`. Rust 1.93+ required (older base images choke on `ar_archive_writer`/`constant_time_eq` edition2024 features).
