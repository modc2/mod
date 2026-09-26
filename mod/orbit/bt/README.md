# bt — the open Bittensor explorer, console + MCP server

Taostats and tao.app are closed-source dashboards on closed indexers. This is
the same job — subnet screener, price charts, validators, account explorer —
as **one open module you can read, run, and fork**, plus the whole protocol
(wallets, TAO transfers, alpha trading) as tools. Three surfaces:

1. **MCP stdio server** — plug the whole protocol into Claude (or any MCP client):

   ```sh
   claude mcp add bittensor -- python3 -m bt.mcp_server
   # or mcpServers config:
   # {"bittensor": {"command": "python3", "args": ["-m", "bt.mcp_server"],
   #                "cwd": "/root/mod/mod/orbit/bt"}}
   ```

2. **MCP over HTTP** — `POST /mcp` speaks the same JSON-RPC (streamable HTTP),
   for remote clients: `https://modc2.com/bt/mcp`.

3. **Web console** — Apple-style single-page app at `/` (gateway: `modc2.com/bt`):
   live market screener (price, 1h/24h/7d change, mcap, 24h volume, liquidity,
   sparklines), per-subnet detail with price chart + identity links + top
   validators, account explorer for any ss58, a **Traders** tab that tracks
   any coldkey over time, a **Chat** tab where an agent answers from those same
   tools and opens what it is talking about, Wallet, Trade, a generic tool
   Console, and a **Docs** section generated live from the tool registry.

## The open indexer

Closed explorers feel instant because a private indexer sits behind them.
`bt/history.py` is ours, in the open, on SQLite: a background thread snapshots
every subnet's pool state on an interval (`BT_REFRESH_SEC`, default 300s) into
`~/.mod/bt/history.db` (`BT_DATA_DIR` to move it). Screener, change-%, real
24h volume (delta of cumulative pool volume), sparklines and charts are all
served from local disk in microseconds — no chain round-trip, no third-party
API, no key. History depth grows the longer it runs. `BT_NO_SNAPSHOT=1`
disables the thread (tests do this).

## Tracking traders

The same idea, pointed at accounts. `bt_track` a coldkey and `bt/traders.py`
snapshots it on an interval (`BT_TRADER_REFRESH_SEC`, default 900s) into
`~/.mod/bt/traders.db`: free TAO plus every alpha position, valued at the
price of the moment. Out of that one table come the equity curve, windowed
PnL, and a **trade tape inferred from the deltas** between snapshots — a
position that grew or shrank by more than 2% *and* 0.05 TAO is a trade;
anything smaller is emission drift and is ignored (flows are marked
`inferred: true`, they are not extrinsics).

It doubles as a time machine other modules borrow: `bt_trader_at` and
`bt_prices_at` answer "what did this account hold, and what was it worth?"
from local SQLite — the questions that otherwise need an archive node.
`orbit/copytensor` runs its entire read path on it (see below).

## Architecture

```
bt/tools.py       ← THE tool registry (37 tools, JSON schemas, handlers)
bt/history.py     ← the open indexer: SQLite snapshots + instant screener
bt/traders.py     ← the trader index: tracked coldkeys, equity, inferred trades
bt/mcp_server.py  ← zero-dep MCP stdio server (JSON-RPC over stdin/stdout)
bt/server.py      ← FastAPI :50280 — app + /api/* + /mcp (starts the indexer)
bt/bt.py          ← engine anchor (Bt chain surface, BtTrader) over _bt_engine.pyc
app/index.html    ← the console (no build step)
```

Every surface is generated from `bt/tools.py`, so the console, the docs, and
the MCP schemas can never drift apart.

## Tools

| Group   | Tools |
|---------|-------|
| Chain   | `bt_subnets` `bt_subnet` `bt_neurons` `bt_neuron_count` `bt_validators` `bt_block` |
| Wallet  | `bt_account` `bt_balance` `bt_wallets` `bt_wallet` `bt_create_wallet`\* `bt_transfer`\* |
| Markets | `bt_screener` `bt_history` `bt_stats` `bt_price` `bt_scan` `bt_leaderboard` `bt_trades` |
| Trading | `bt_portfolio` `bt_trader_balance` `bt_buy`\* `bt_sell`\* `bt_sell_all`\* `bt_swap`\* |
| Traders | `bt_track` `bt_untrack` `bt_traders` `bt_trader_board` `bt_trader` `bt_trader_history` `bt_trader_flows` `bt_trader_snapshot` `bt_trader_at` `bt_prices_at` |
| Network | `bt_sync` `bt_rpc_health` `bt_best_rpc` |
| Console | `bt_view` — opens a view in the console the caller is looking at |

\* = real on-chain write (moves TAO or creates key material). The MCP server's
instructions tell clients to confirm with the user first; the console asks
before signing.

`bt_screener` / `bt_history` / `bt_stats` answer instantly from the indexer;
`bt_scan` is the raw full-chain scan (slow, but always straight from chain).
`bt_account` explores **any** ss58 — free TAO plus every alpha position with
live valuation. `bt_traders` / `bt_trader` / `bt_trader_flows` are the tracked
side of that: instant, from the trader index.

`bt_trader_board` ranks that whole index over N days in one SQL pass (~260ms
for 141 traders). It splits each trader's PnL into what the book earned on
price and what was staked in or out —

    market = Σ alpha_start · (price_end − price_start)
    flow   = Σ (alpha_end − alpha_start) · price_end

— because ranking on the raw percentage crowns whoever wired stake in most
recently. `market_pct` is the column that measures trading, so it is the
default sort. A trader tracked for less than the window is ranked over the
history that exists and says so in `window_days`; one with a single snapshot
comes back `baseline: false` at PnL 0, never a fabricated number.

## Who else reads this

`orbit/copytensor` (dTAO copy trading) no longer walks public RPCs for reads —
`src/chain/bt_source.py` wraps this module's `POST /api/call` in a
`SubtensorClient` whose subnet, position and history reads come from here, and
which falls back to its own RPC pool if bt is stopped. Its `/subnets` went
from a multi-second `all_subnets()` walk to ~150ms, and its PnL baselines come
from `bt_trader_at` instead of an archive node. Its leaderboard is now one
`bt_trader_board` call — 260ms against the 211s its own per-account archive
walk took over a 253-account pool. Point it elsewhere with
`COPYTENSOR_BT_URL`; turn it off with `COPYTENSOR_BT=0`.

## API

```
GET  /api          module info
GET  /api/tools    MCP-shaped tool listing
GET  /api/docs     grouped docs (drives the Docs section)
POST /api/call     {"tool": "bt_screener", "args": {"limit": 5}}
POST /mcp          MCP JSON-RPC (initialize / tools/list / tools/call)

GET  /.well-known/agent.json   the agent card (also /api/agent/card)
GET  /api/agent/status         auth, model, tool count, runs in flight
GET  /api/agent/tools          the agent's toolbox, grouped
GET  /api/agent/chats          conversations · /api/agent/chats/{id} one, with messages
POST /api/agent/chat           {"message", "chat", "context"} -> SSE run
POST /api/agent/ask            the same turn, run to completion, one JSON reply
POST /api/agent/stop           {"chat"} -> kill the run in flight
```

## Chat — the agent protocol

The **Chat** tab is a conversation with a Claude agent (`bt/agent.py`) whose
only toolbox is this module's own MCP server: every answer is a run of tool
calls against the live chain and the local index, streamed back token by
token. It speaks the fleet's agent protocol (`agent/1.0`) — a card at
`/.well-known/agent.json` says who it is, what it can do and how to talk to
it, and any client can hold the same conversation the console does.

```sh
curl -s localhost:50280/.well-known/agent.json | jq .        # who am I talking to
curl -sN localhost:50280/api/agent/chat -H 'content-type: application/json' \
  -d '{"message":"which subnet pumped hardest today?"}'      # SSE run
curl -s localhost:50280/api/agent/ask -H 'content-type: application/json' \
  -d '{"message":"how stale is the index?","chat":"<id>"}'   # one JSON reply
```

- **Multi-turn.** Every conversation carries a Claude session id; pass `chat`
  and the next turn resumes it. The transcript — messages, the tools each
  answer played, what the run cost — is kept in `~/.mod/bt/chats.db` and
  served from `/api/agent/chats`, so a chat survives a reload or a restart.
- **It drives the console.** `bt_view` is the one tool that touches no chain:
  it opens the screener, a subnet with its chart, a trader or an account on
  the screen of whoever is asking. The run emits a `view` event, the console
  applies it, and the chip in the transcript replays it. The browser sends
  back what it is looking at as `context`, so "and this one?" has a referent.
- **Streamed.** Events are `start`, `status`, `text_delta`, `text`, `tool`,
  `tool_done`, `view`, `done`, `error`. `POST /api/agent/stop` kills a run in
  flight; the partial answer is kept.
- **Read-only.** The six on-chain writes are denied by name, and so are the
  CLI's own built-in tools — the agent can read anything and sign nothing.
  Trading stays in the Trade tab, where a person signs it.

All parameters (model, max turns, timeout, streaming) sit at the top of
`bt/agent.py`. Auth resolves from `ANTHROPIC_API_KEY`, then
`~/.mod/bt/anthropic.key` (auto-created 0600 if nothing else exists), then the
Claude CLI's own login.

## Index reads never wait on the chain

Tools that answer from the local index or disk are marked `local` in the
registry and skip the websocket lock that serializes chain reads: a console
page load, or an agent's four screener calls, no longer queue behind a
40-second `bt_scan`. The nearest-snapshot lookups behind the screener seek an
index instead of ordering a million rows by distance, which took the screener
from ~1s to ~0.1s, and `trader_snaps` is indexed by time (`MAX(ts)`,
`COUNT(*)` and every window scan used to read the whole 10 GB table).

## Run

```sh
pm2 start "python3 -m bt.server" --name bt-app --cwd /root/mod/mod/orbit/bt
```

Network defaults to `finney` mainnet; chain tools take an optional `network`
arg (`test`, or a custom endpoint). Wallets live in `~/.bittensor/wallets`.

## Tests

```sh
python3 -m pytest tests/ -q
```

Tests cover the registry, the indexer (synthetic snapshots — change %, volume
deltas, sparklines, downsampling, cold-start-from-disk), the trader index
(flow inference, dust rejection, PnL windows, snapshot tolerance), MCP stdio
protocol, and HTTP surfaces without touching the chain.
