---
name: hyperliquid
description: Copy-trade any Hyperliquid wallet by N-day performance, compose them into vault-backed indexes, and drive it all over REST or MCP
---

# hyperliquid

Full-stack Hyperliquid integration: backend agent wallet that signs every
action type, autonomous copy-trade live engine, trader analytics + indexes
+ vault helpers, plus a Next.js UI.

```
src/
  mod.py        # high-level orchestrator (serve/kill/status/forward)
  api/          # Rust API (axum + tokio) — the hot path
  app/          # Next.js 14 frontend
```

## Quick start

```python
import mod as m
hl = m.mod('hyperliquid')()

hl.build()                     # cargo build --release
hl.serve()                     # api on 8919, app on 3919
hl.status()                    # service + api health
hl.kill()                      # stop both
```

## What it does

- **Top Traders** — paginate the HL leaderboard, hydrate each candidate's
  fills inside an N-day window, score by pnl / volume / win-rate / Sharpe.
  Mirrors the same activity-based scoring used by `polymarket/active-traders`.
- **Copy follows** — register a `follower → leader` relationship with
  size-pct, per-trade caps, allow/deny coin lists. The Rust engine polls
  each leader and emits scaled "signals" you can sign + submit.
- **Indexes** — pick N traders, weight them, optionally auto-build
  (`autoIndex` weights by ∝ pnl). Backtest weighted PnL over the window.
- **Private vaults** — for any index you own, generate a `createVault`
  action payload, sign with your owner key, and link the resulting
  vault address. Only the owner can deposit/withdraw — the index then
  routes signals through it.

## API surface

`mod.py` exposes everything as forwardable fns. Highlights:

```python
hl.top_traders(days=7, pool=200)
# The whole gated universe (~5k wallets, priced from the leaderboard for free),
# fill stats for the top 250 by ROI only, filtered to sharpe >= 1 and ordered by it.
hl.top_traders(days=7, pool='all', enrich=250, min_sharpe=1.0, sort='sharpe')
# `coins` is a requirement, not a filter: the scan walks the ranked leaderboard
# until it holds `pool` active wallets that traded one of them (see `depth`).
hl.top_traders(days=7, pool=50, coins=['ZEC', 'ENA'])
hl.analyze_trader('0xabc…', days=14)

hl.create_index(name='Top10', owner='0x…', legs=[
    {'address': '0x…', 'weight': 0.3},
], days_window=7, notional_pct=50)
hl.index_perf(idx_id, days=7)
hl.vault_intent(idx_id, initial_usd=100)        # returns sign-this payload

hl.create_follow(follower='0x…', leader='0x…', size_pct=10)
hl.list_signals(follower='0x…')
```

The same operations are reachable via `POST /forward` on the Rust API
for keyless mod-protocol consumers.

## Auth

Public reads (market data, leaderboards, trader analysis, vaults, index
browsing, and the `POST /indexes/auto` basket *preview* — it ranks the open
leaderboard and stores nothing) are open. Everything wallet-scoped — follows,
signals, signer, trading, transfers, live engine, and saving a strat — needs a
mod protocol-auth token as `Authorization: Bearer <token>`.

**You usually don't have to supply one.** `m.mod('hyperliquid')()` mints its
own from `m.key()` on first gated call and re-mints before it goes stale, so
Python and MCP callers are authenticated out of the box, as the node's key.
Override with `m.mod('hyperliquid')(token=…)`, `(key='name')`,
`$HYPERLIQUID_TOKEN` or `$HYPERLIQUID_KEY`. `hl.whoami()` says which address
the API sees — the first thing to check when a write is refused.

`owner` and `follower` are optional on `create_index` / `create_follow`: the
API takes them from the token that signed the request. Send them only to be
explicit, and they must match — the gate pins every `eoa`/`follower`/`owner`
in a query or body to the token's own address.

### Refusals are part of the API

A 401/403 answers with a stable `reason`, a `message` written for a person,
and `sign_in` — whether a fresh signature would fix it:

```json
{ "error": "unauthorized", "reason": "expired_token", "sign_in": true,
  "message": "Your session expired. Sign in again to continue.",
  "detail": "sign-in is 29d old, and sessions last 7d" }
```

| reason | status | sign_in | means |
| --- | --- | --- | --- |
| `no_token` | 401 | yes | no Authorization header |
| `expired_token` | 401 | yes | past the session window (7d, `$HYPERLIQUID_SESSION_TTL`) |
| `bad_token` | 401 | yes | malformed, or the signature doesn't recover to its key |
| `wrong_wallet` | 403 | no | you named an address your token doesn't sign for |
| `not_owner` | 403 | no | the strat/follow belongs to another wallet |
| `unscoped_query` | 403 | no | a per-wallet list with no `?follower=` / `?eoa=` |

Clients branch on `reason` and show `message`. The web console re-mints and
replays a write **once** when `sign_in` is true, so an expired session costs
one wallet prompt rather than a dead end; GETs never trigger a prompt, so a
background poll can't nag. `GET /auth/me` returns the address behind a token
(and 401s when it's dead) — the console calls it on load so the header can't
claim "Signed in" over a session the server has already dropped.

`HYPERLIQUID_ACCESS_OPEN=1` disables the gate for local dev / CI.

## MCP tool server

The whole fn surface is also an MCP server — one tool per mod fn,
named `hl_<fn>`. Three transports, so a client connects with whatever it
already speaks:

| transport | how | for |
| --- | --- | --- |
| streamable HTTP | `POST /mcp` — one JSON-RPC message *or batch* per POST | current clients |
| HTTP+SSE | `GET /sse` → its first event names `POST /messages?sessionId=…` | the 2024-11-05 transport many agent frameworks still ship |
| stdio | `hyperliquid-api --stdio` | child-process clients, no network hop |

```bash
claude mcp add --transport http hyperliquid https://<host>/api/hyperliquid/mcp
claude mcp add --transport sse  hyperliquid https://<host>/api/hyperliquid/sse
claude mcp add hyperliquid -- src/api/target/release/hyperliquid-api --stdio
```

The `/hyperliquid/mcp` page in the app is the connect surface for humans:
live endpoints for the origin you are on, paste-ready config per client
(optionally carrying your own token), and the searchable tool list.

```python
hl.mcp()                     # tool schema + the mod-protocol mapping
hl.mcp_tools()               # [{name, fn, route, public, bound}, …]
hl.mcp_call('hl_top_traders', days=7, pool=50)
hl.mcp_config()              # client config snippets (http / sse / stdio / gateway)
```

`GET /mcp/schema` is the bridge between the two protocols: every tool
publishes the `fn` it fronts and the REST route that fn calls, so an MCP
client and a mod client see the same module. Tool calls execute as loopback
requests against this API carrying the caller's own `Authorization` header —
the auth gate treats MCP exactly like browser traffic, and MCP grants no
authority of its own. A unit test asserts every tool's `fn` is declared in
`config.json`, so the two schemas cannot drift.

## Ask — the agent that drives that MCP server

`src/agent.py` runs a Claude agent whose *only* toolbox is the MCP server
above, so it answers from live tool calls instead of memory. UI: `/ask`.

```python
hl.ask('who are the top 5 traders by 7-day ROI, and what do they hold?')
# → {answer, tools: [{name, args}, …], turns, ms, cost_usd}
hl.ask('close my BTC position', act=True)   # write tools, needs a token
hl.ask_status()                             # model auth, tool counts, hints
```

```bash
curl -N /api/hyperliquid/ask -H "Authorization: Bearer $TOKEN" \
     -d '{"question":"best APR vault over $1M TVL?"}'   # SSE event stream
```

Two guarantees hold it in place:

* **No new authority.** The caller's token rides to the stdio MCP server as
  `HYPERLIQUID_TOKEN`, every tool re-enters this API over its REST routes,
  and `auth.rs` gates it. The agent can never read or do more than the
  caller could by hand. `POST /ask` itself needs a token (it spends model
  credits); `GET /ask/status` is public.
* **A question cannot trade.** The allow/deny lists come from
  `GET /mcp/schema`: GET-backed tools are reads, everything else is a write.
  Reads-only is the default; writes need `act=true` *and* a token. Local
  host tools (Bash/Read/Write/…) are denied in both modes.

Model auth resolves ANTHROPIC_API_KEY → `~/.mod/hyperliquid/anthropic.key`
(created 0600 on first run) → Claude CLI OAuth. Knobs: `HL_AGENT_MODEL`
(sonnet), `HL_AGENT_MAX_TURNS` (16), `HL_AGENT_TIMEOUT` (300s).

## Backend agent wallet

The Rust API generates an encrypted-at-rest ECDSA key per master EOA. The
user signs `approveAgent` once in their browser wallet; after that the
backend signs every order/cancel/modify/leverage/vault-transfer/etc. on
their behalf — no browser tab needed.

```python
agent = hl.signer_address(eoa)                # → "0xagent..."
intent = hl.approve_agent_intent(eoa)         # → {action, digest, nonce, agentAddress}
# user signs `intent.digest` with their wallet, then forward to /exchange:
hl.forward(fn='exchange_post', payload={
    'action': intent['action'],
    'nonce':  intent['nonce'],
    'signature': {'r': '0x..', 's': '0x..', 'v': 28},
})
```

Master key for the AES-encrypted keystore is sourced from
`HYPERLIQUID_SIGNER_MASTER_KEY` or persisted to `<HYPERLIQUID_DATA_DIR>/signer-store/.master`.

## Trading

Once the agent is approved, every action signs server-side:

```python
hl.trade(eoa, coin='ETH', is_buy=True,  size=0.01)             # market (IOC + 100bps slip)
hl.trade(eoa, coin='ETH', is_buy=True,  size=0.01, price=2900) # GTC limit
hl.trade(eoa, coin='ETH', is_buy=False, size=0.01, reduce_only=True)
hl.cancel(eoa, [{'coin':'ETH','oid': 12345}])
hl.modify(eoa, oid=12345, coin='ETH', is_buy=True, price=2905, size=0.01)
hl.set_leverage(eoa, 'ETH', 10)
hl.update_isolated_margin(eoa, 'ETH', is_buy=True, amount_usd=50)
hl.schedule_cancel(eoa, time_ms=int(time.time()*1000) + 3600_000)  # dead-man switch
```

## Funding: getting money in from another chain

Hyperliquid's own bridge only credits USDC on Arbitrum, but LI.FI exposes
Hyperliquid Core as a routing destination — so **one** signed transaction on
Ethereum, Arbitrum, Base, OP Mainnet, Polygon, BNB Chain or Avalanche lands
as USDC in the perps account. No Arbitrum layover, no second wallet prompt.

`/deposit/balances` scans every chain in a single Multicall3 call each and
returns a flat `sources[]` of spendable (chain, token) pairs — USDC, USDT,
DAI, WETH/WBTC and the chain's native coin — priced off Hyperliquid's own
mids and sorted richest first. `max` already reserves native gas. A token
whose price can't be read comes back `priceUsd: null`, never `0`, so it
stays selectable.

```python
hl.deposit_chains()                    # chains + the tokens accepted on each
hl.deposit_balances(eoa)               # {'sources': [...], 'chains': [...]}
q = hl.deposit_quote(from_chain_id=8453, token='usdc', amount='250', eoa=eoa)
#  → {'toUsdc': 249.9, 'feeUsd': .06, 'gasUsd': .01, 'durationSec': 1080,
#     'landsOnHyperliquid': True, 'approvalAddress': ..., 'transactionRequest': {...}}
# the wallet signs transactionRequest (approving `approvalAddress` first for
# an ERC-20 source); nothing moves until it does.
hl.deposit_status(tx_hash, from_chain_id=8453)   # PENDING → DONE + receivedUsdc
```

`token` takes `"usdc"`, `"native"`, a symbol (`"WETH"`) or a token address.
USDC already on Arbitrum skips the router entirely — the browser sends a
plain ERC-20 transfer to the bridge, which costs nothing extra. Set
`to_chain_id` to reverse the direction: that quotes Arbitrum USDC out to
another chain, which is how withdrawals reach anywhere but Arbitrum.

## Transfers on Hyperliquid

```python
hl.usd_class_transfer(eoa, amount='100', to_perp=True)   # spot → perp
hl.vault_transfer(eoa, vault='0xvault...', is_deposit=True, amount_usd=100)
hl.withdraw(eoa, destination='0xL1addr', amount='50')    # pays out on Arbitrum
hl.usd_send(eoa, destination='0xanotherUser', amount='10')
hl.spot_send(eoa, destination='0x...', token='PURR:0x...', amount='1.5')
```

## Live copy-trade engine

Long-running per-EOA tokio task that polls leader fills and mirrors them
through the backend agent. Auto-resumes on API restart from
`<HYPERLIQUID_DATA_DIR>/live-engine/<eoa>.config.json`.

```python
hl.live_start(eoa,
    traders=[{'address':'0xLeader...','weight':1.0}],
    interval_ms=15000,
    size_pct=10,                # mirror leader.size × 10%
    max_per_trade_usd=200,
    min_order_size_usd=10,
    max_slippage_bps=100,
    coins_allow=['ETH','BTC'],  # optional whitelist
    vault_address='0xvault...', # optional: route via vault
)
hl.live_status(eoa)             # cycle count, observed trades, orders placed/failed
hl.live_stop(eoa)
```

## Vault create payload

`vault_intent(index_id, initial_usd)` returns:

```json
{
  "action": {"type":"createVault","name":"…","initialUsd":100000000,"nonce":...},
  "owner": "0x…",
  "exchange_url": "https://api.hyperliquid.xyz/exchange"
}
```

The Rust binary stays keyless on purpose — signing happens in the
caller (browser wallet, SDK, etc.). The `/forward` passthrough then
relays the signed payload to Hyperliquid's `/exchange`.

## Ports

| service  | port | env override         |
| -------- | ---- | -------------------- |
| Rust API | 8919 | `PORT`               |
| Next app | 3919 | passed by `serve`    |
| Testnet  | —    | `HYPERLIQUID_TESTNET=true` |
