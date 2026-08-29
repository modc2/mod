# debank

**What an address actually owns.**

A block explorer tells you what transactions happened. DeBank tells you what a
wallet *holds right now* — across every EVM chain, including the parts that
aren't in the wallet: LP positions, staked and locked balances, lending
collateral, and the debt sitting against it. This module puts that behind the
mod protocol as **eighteen MCP tools**, a REST API and a browser console, all
over one port and one client, so an agent, a shell and a human never get
different answers to the same question.

```
m debank/portfolio 0xd8da6bf26964af9d7eed9e03e53415d37aa96045          # net worth, and which chains hold it
m debank/tokens    0xd8da…6045 chain=eth                               # priced balances, biggest first
m debank/protocols 0xd8da…6045                                         # DeFi positions, net of borrowing
m debank/approvals 0xd8da…6045 chain=eth                               # who can still take it
m debank/history   0xd8da…6045 chain=eth                               # decoded transactions
m debank/chains                                                        # works without a key
```

## Tool calling

The eighteen tools are the point of the module. They are ordered the way the
question actually gets answered — start wide, then drill only where the money
is:

| tool | what it answers |
| --- | --- |
| `debank_portfolio` | net worth, and **which chains carry it** — the first call; its chain list is what you pass to everything else |
| `debank_tokens` | wallet balances, priced and ranked, dust dropped **and counted** |
| `debank_protocols` | open DeFi positions: supplied + rewards **minus borrowed**, with health rate |
| `debank_approvals` | standing approvals ranked by `exposure_usd` — what a spender could take *today* |
| `debank_history` | decoded transactions: what moved, through what protocol, at what gas |
| `debank_nfts` | NFTs at floor price where DeBank has one |
| `debank_net_curve` | net worth over time, and the change across the window |
| `debank_position` | one protocol position in full, unsummarized |
| `debank_chains_used` | which chains this address has ever touched — one cheap call |
| `debank_protocol` | the protocol catalog by TVL, or one protocol by id |
| `debank_token` / `debank_token_price` | token metadata and price, now or on a past date |
| `debank_holders` | biggest holders of a token, or biggest depositors in a protocol |
| `debank_gas` | the current gas market on a chain |
| `debank_chains` | every chain DeBank indexes — **the one tool that answers signed-out** |
| `debank_account` | does the key work, where did it come from, what's left on it |
| `debank_set_key` | store an AccessKey off-tree at 0600 |
| `debank_raw` | escape hatch: any Cloud API route with your key attached |

Three transports, one registry:

```bash
python3 mcp.py                    # stdio — for Claude Code / Desktop
curl -X POST localhost:50720/mcp \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'   # Streamable HTTP
m debank/mcp_call tool=debank_portfolio id=0x…          # in-process, no server
```

`m debank/mcp_config` prints a drop-in client config for both transports.

## Design decisions worth knowing

**Dust is dropped, never silently.** Every list route returns
`hidden_below_min_usd` alongside the rows, and the totals are computed *before*
filtering. A portfolio that looks empty at `min_usd=1` says how many rows it hid.

**Debt is subtracted.** `debank_protocols` reports supplied + unclaimed rewards
**minus** borrowed, so a leveraged position can be worth less than its deposits.
The raw API leaves you to work that out yourself.

**Approvals are ranked by what's actually at risk.** An infinite allowance on an
empty token balance is noise; a capped allowance on a large balance is not.
`exposure_usd` is `min(allowance, balance) × price`, which is the number that
decides which approval to revoke first.

**Spam tokens are excluded by default.** Their prices are fiction, and a total
that includes them is fiction too. `all_tokens=true` opts back in, and says so
in the response `note`.

**Chain names are translated.** `ethereum` → `eth`, `polygon` → `matic`,
`gnosis` → `xdai`, `optimism` → `op`. Unknown values pass through untouched so a
new chain works the day DeBank adds it.

**Addresses only.** DeBank indexes 0x addresses, not ENS. Passing `vitalik.eth`
returns a 400 that says so rather than an empty portfolio that looks like a
broke wallet.

## Keys — BYOK

This module holds **no house key**. Every call spends the caller's own DeBank
units, billed per call by [cloud.debank.com](https://cloud.debank.com).

```
explicit key argument
→ x-debank-key request header
→ DEBANK_ACCESS_KEY / DEBANK_API_KEY
→ ~/.mod/debank/key.json          (0600, off-tree, written by m debank/set_key)
```

An `Authorization: Bearer …` header is **never** read as a DeBank key — the
gateway puts its own session tokens there, and forwarding one upstream would
leak it.

`debank_chains` is the exception: with no key it falls back to DeBank's public
catalog and labels the answer `source: "public"`.

## Run it

```bash
m debank/serve            # pm2: debank-api on :50720 — api, console and MCP
m debank/status           # is it up, does the key resolve
m debank/kill
m debank/test             # 33 offline tests, no key and no network needed
```

| surface | url |
| --- | --- |
| REST | `http://localhost:50720/` (lists every route) |
| console | `http://localhost:50720/debank` |
| MCP | `POST http://localhost:50720/mcp` |

The server strips `/debank/_api`, `/api/debank` and `/_api` itself, and the
console derives its own URLs from `location`, so all three work whether the
module is hit directly or through the gateway.

## Layout

```
mod.py          the anchor — every fn, plus serve/kill/status/test
client.py       DeBank Cloud client: key resolution, retries, and the
                normalizers that turn nested amounts+prices into flat USD rows
mcp.py          tool registry + JSON-RPC 2.0 (stdio and Streamable HTTP)
api.py          REST + MCP + console on one port
console.html    browser console — one address in, eight views out
test/           offline tests over fixtures: the money math, the MCP protocol,
                key handling, and REST/MCP parity
```

Python stdlib only. No dependencies.

## State of the module

Verified live on this box: the server, the console, all three MCP transports,
gateway prefix stripping, the keyless chain catalog, and every normalizer and
protocol path under 33 offline tests.

**Not verified against a live key** — no DeBank AccessKey is set on this box, so
every authenticated route (portfolio, tokens, protocols, approvals, history,
NFTs) has been exercised only against fixtures shaped like the documented Cloud
API responses. The paths follow DeBank's published routes; if one has drifted,
it will surface as a 404 with a hint pointing at `debank_raw`.
