# hyperliquid

Full-stack Hyperliquid integration for the mod protocol: a Rust (axum) API on
port **8919**, a Next.js 14 frontend on **3919**, and a Python orchestrator
(`src/mod.py`) that builds, serves, and forwards into both. It covers trader
analytics, copy-trade follows with a live mirroring engine, weighted trader
indexes backed by private vaults, a backend agent wallet that signs every
Hyperliquid action type, and cross-chain deposits via LI.FI.

> The authoritative, always-current reference is [`skill.md`](../skill.md) at
> the module root, plus `config.json` for the full `fns` list (90 forwardable
> functions). This file is an orientation, not the contract.

## Layout

```
src/
  mod.py        # high-level orchestrator (build/serve/kill/status/forward)
  api/          # Rust API (axum + tokio) — the hot path, port 8919
  app/          # Next.js 14 frontend, port 3919
```

## Quick start

```python
import mod as m
hl = m.mod('hyperliquid')()

hl.build()      # cargo build --release
hl.serve()      # api on 8919, app on 3919 (pm2)
hl.status()     # service + api health
hl.kill()       # stop both
```

There are no API keys or secrets in this module's interface. Reads are open;
writes are authenticated with a **mod protocol-auth token** — a wallet-signed
envelope sent as `Authorization: Bearer <token>`. Trading actions are signed
either in the caller's browser wallet or by the module's backend agent wallet
(an HL agent key the user approves once with `approveAgent`), stored encrypted
under `~/.hyperliquid/signer-store/`.

## What it does

- **Top traders** — paginate the HL leaderboard, hydrate each candidate's
  fills inside an N-day window, score by pnl / volume / win-rate / Sharpe.
  `top_traders(days=7, pool='all', enrich=250, min_sharpe=1.0, sort='sharpe')`.
- **Copy follows** — register `follower → leader` with a size percentage,
  per-trade USD caps, and coin allow/deny lists. The Rust engine polls each
  leader's fills and emits scaled *signals*; the live engine can mirror them.
- **Indexes** — weight N traders into a basket, backtest windowed PnL,
  auto-build by performance, and back an index you own with a private vault
  (`vault_intent` returns the `createVault` payload for you to sign).
- **Deposits** — fund the HL perps account from twelve EVM chains in one
  transaction, routed by LI.FI; balances are scanned via Multicall3.
- **MCP** — the same fn surface is exposed as MCP tools (`POST /mcp`,
  schema at `GET /mcp/schema`); tool calls loop back through the REST
  surface so auth applies identically.

## Calling it

Every fn in `config.json` is a method on the `Hyperliquid` class in
`src/mod.py`, and the same operations are reachable over HTTP:

```python
hl.top_traders(days=7, pool=200)
hl.analyze_trader('0xabc…', days=14)
hl.create_follow(follower='0x…', leader='0x…', size_pct=10)
hl.list_signals(follower='0x…')
hl.create_index(name='Top10', legs=[{'address': '0x…', 'weight': 0.3}])
```

Generic passthrough for mod-protocol consumers:

```
POST /forward   {"fn": "top_traders", "payload": {"days": 7, "pool": 150}}
```

Useful REST reads (public, no token):

```
GET /leaderboard
GET /traders/top?days=7&pool=all&rank=roi&enrich=120&sort=sharpe
GET /trader/{address}/curve?days=7
GET /orderbook/{coin}         GET /candles/{coin}?interval=1h
GET /vaults                   GET /indexes
GET /deposit/chains           GET /deposit/balances?eoa=0x…
```

Wallet-scoped routes (`/follows`, `/signals`, `/live/*`, `/agent/*`,
`/invest`, trading and transfers) require the Bearer token, and list routes
must be scoped to the signed-in wallet (e.g. `?follower=0x…`).

## Notes

- Test on testnet first (`HL_TESTNET=1` / `testnet=True`) before real funds.
- Data lives outside the tree at `~/.hyperliquid/` (`HYPERLIQUID_DATA_DIR`).
- Set `HL_CORS_ORIGINS=https://your.host` to pin browser access to known
  frontends; unset allows any origin (tokens are never sent ambiently).

## License

MIT
