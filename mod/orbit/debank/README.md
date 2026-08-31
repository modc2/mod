# debank

**Your wallet, as a bank.**

Open `/debank`, connect the browser wallet (MetaMask, Rabby, Coinbase Wallet —
any EIP-1193 provider) and the console becomes an account: total balance across
every chain, one account per chain, send and receive native coin or stablecoins,
a statement of decoded history, and the standing permissions that let someone
else move your money — with one-click revoke. The private key never leaves the
wallet: this page builds `transfer` and `approve(spender, 0)` transactions and
the wallet signs them. Paste any other `0x` address to watch it instead.

Two floors under the bank:

- **DeBank Cloud** — the full picture (every token, DeFi net of debt, NFTs,
  history, approvals). BYOK: the key lives in the browser and is sent only to
  this module, or is stored off-tree with `m debank/set_key`.
- **The bank rail** — native coin + USDC/USDT/DAI on Ethereum, Base, Arbitrum,
  Optimism, Polygon, BNB Chain, Avalanche and Gnosis, read straight from public
  RPCs and priced by CoinGecko. **Needs no key**, so the bank always shows real
  numbers; `source: "rpc"` and `coverage` say exactly what was looked at.

## The savings desk — index funds for the account

Open **Savings** and the idle stablecoins become the account's savings, ready
to be placed into **index funds**: curated, weighted baskets of yield venues —
Aave V3, Compound V3, Morpho's Steakhouse vaults, Maple's syrupUSDC, Sky's
sDAI and Spark's savings vault — each fund in **one asset on one chain**, so
placing it is a handful of wallet signatures. Every card shows:

- **Projected ROI** — the 30-day mean APY per sleeve, weighted, joined live
  from DefiLlama (through the local `defi` module when it is up, straight from
  `yields.llama.fi` otherwise, frozen hints as the marked last resort);
- **Liquidity locked in each protocol** — `totalAssets` / receipt
  `totalSupply` read from the venue contract *right now*, next to the pool's
  DefiLlama TVL, plus the withdrawable buffer for Aave/Compound;
- **Exit terms** — instant vs by-request, with the slowest sleeve named.

“Place savings” builds the plan (`GET /savings/plan`): per sleeve an
**exact-amount** `approve` (with USDT's reset-to-zero leg only when needed)
then the deposit — ERC-4626 `deposit`, Aave `supply`, Comet `supply` — and the
wallet signs each one. The server signs nothing and holds nothing; holdings
are read back keyless (`balanceOf` + `convertToAssets` over public RPCs), and
each placed leg is noted in an off-tree ledger at `~/.mod/debank/savings/`.
Every venue address in `funds.json` was verified on chain
(`symbol()`/`asset()`/`baseToken()`) before it was written down.

```
m debank/funds amount=10000                   # every fund, projected at $10k
m debank/fund fund=core-usdc-base amount=500  # one fund in full
m debank/savings 0xd8da…6045                # idle vs placed, from chain
m debank/savings_plan id=0x… fund=yield-plus-usdc-eth amount=2500
```

## What an address actually owns

A block explorer tells you what transactions happened. DeBank tells you what a
wallet *holds right now* — across every EVM chain, including the parts that
aren't in the wallet: LP positions, staked and locked balances, lending
collateral, and the debt sitting against it. This module puts that behind the
mod protocol as **twenty-four MCP tools**, a REST API and a browser console, all
over one port and one client, so an agent, a shell and a human never get
different answers to the same question.

```
m debank/portfolio 0xd8da6bf26964af9d7eed9e03e53415d37aa96045          # net worth, and which chains hold it
m debank/tokens    0xd8da…6045 chain=eth                               # priced balances, biggest first
m debank/protocols 0xd8da…6045                                         # DeFi positions, net of borrowing
m debank/approvals 0xd8da…6045 chain=eth                               # who can still take it
m debank/history   0xd8da…6045 chain=eth                               # decoded transactions
m debank/balances  0xd8da…6045                                         # keyless: native + stables, 8 chains
m debank/networks                                                      # keyless: what a wallet needs to switch
m debank/chains                                                        # works without a key
```

## Tool calling

The twenty-four tools are the point of the module. They are ordered the way the
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
| `debank_chains` | every chain DeBank indexes — answers signed-out |
| `debank_balances` | **keyless** — native + USDC/USDT/DAI on the 8-chain bank rail via public RPCs; the fallback when portfolio 401s |
| `debank_networks` | **keyless** — chain ids (dec + hex), RPCs, explorers, stablecoin contracts: what `wallet_switchEthereumChain` / an ERC-20 transfer needs |
| `debank_funds` | **keyless** — the savings index funds: curated baskets of yield venues with live projected ROI and per-protocol locked liquidity read from chain |
| `debank_fund` | **keyless** — one fund in full; `venue:<id>` is a fund of one |
| `debank_savings` | **keyless** — idle stablecoins vs money already placed in each venue, with blended APY and projected yearly income |
| `debank_savings_plan` | **keyless** — the exact approve+deposit transactions the owner's wallet must sign, per sleeve |
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
