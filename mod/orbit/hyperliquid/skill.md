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
hl.top_traders(days=7, min_per_day=1, pool=200)
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
browsing) are open. Everything wallet-scoped — follows, signals, signer,
trading, transfers, live engine — needs a mod protocol-auth token as
`Authorization: Bearer <token>`, and any `eoa`/`follower`/`owner` you pass
must be that token's own address. Pass it as `m.mod('hyperliquid')(token=…)`
or `HYPERLIQUID_TOKEN`; `HYPERLIQUID_ACCESS_OPEN=1` disables the gate for
local dev.

## MCP tool server

The whole fn surface is also an MCP server — 53 tools, one per mod fn,
named `hl_<fn>`. Transports: `POST /mcp` (Streamable HTTP, JSON-RPC 2.0,
one message per POST) and stdio.

```bash
claude mcp add hyperliquid -- src/api/target/release/hyperliquid-api --stdio
# or point an HTTP MCP client at  /api/hyperliquid/mcp
```

```python
hl.mcp()                     # tool schema + the mod-protocol mapping
hl.mcp_tools()               # [{name, fn, route, public, bound}, …]
hl.mcp_call('hl_top_traders', days=7, pool=50)
hl.mcp_config()              # client config snippets (stdio / http / gateway)
```

`GET /mcp/schema` is the bridge between the two protocols: every tool
publishes the `fn` it fronts and the REST route that fn calls, so an MCP
client and a mod client see the same module. Tool calls execute as loopback
requests against this API carrying the caller's own `Authorization` header —
the auth gate treats MCP exactly like browser traffic, and MCP grants no
authority of its own. A unit test asserts every tool's `fn` is declared in
`config.json`, so the two schemas cannot drift.

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

## Transfers / bridging

```python
hl.usd_class_transfer(eoa, amount='100', to_perp=True)   # spot → perp
hl.vault_transfer(eoa, vault='0xvault...', is_deposit=True, amount_usd=100)
hl.withdraw(eoa, destination='0xL1addr', amount='50')
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
