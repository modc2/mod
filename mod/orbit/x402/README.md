# x402 · every paid API, one local index

x402 turns HTTP 402 into a real payment step. A resource answers an unpaid
request with its price; the client pays (USDC on Base, Solana, Algorand, and
others) and retries. No single directory of these resources exists, because
each facilitator keeps its own. This module finds the facilitators, reads
every list they publish, and merges the results into one SQLite file on this
box.

```
m x402/sync                                  # discover facilitators, crawl all
m x402/services q="image generation" max_price=0.05 sort=cheap
m x402/find q="turn a pdf into text"         # by meaning, not words
m x402/mcp_servers q="is this token a rug"   # the right paid MCP server
m x402/service url=https://api.exa.ai/search
m x402/hosts                                 # providers, busiest first
m x402/sources                               # facilitators + crawl state
m x402/facilitators                          # registry facilitators, listing or not
m x402/partners category=Facilitators        # coinbase/x402 ecosystem registry
m x402/probe url=https://... method=POST     # ask one URL for its 402, pin it
m x402/add_source base_url=https://my-facilitator.example
m x402/serve                                 # console + API on :51110
```

Console: `http://localhost:51110/x402/`. API: `/x402/api/{fn}` (also
`/api/x402/{fn}` and bare `/{fn}` locally).

## Search by meaning (and the right MCP server)

`find` ranks services by what they do. Each service gets one 384-dim vector
(`all-MiniLM-L6-v2`, CPU) over its name, description, URL path words and tags.
The vectors are stored as float32 blobs in the same SQLite file (`vectors`
table) and held in RAM as one matrix, so a query costs one encode plus one
matrix product. The score is `0.74·cosine + 0.22·FTS bm25 + 0.04·usage`; the
FTS term keeps exact names and identifiers winning.

`mcp_servers` (or `find kind=mcp`) keeps only the paid resources that live
under an MCP endpoint: a `/mcp` path segment, an `mcp.*` host, or a listing
with `type: mcp`. It then folds those tools into their server. A server's
score is its best tool plus a little for each other tool that matches. Every
row includes a `claude mcp add --transport http …` line.

- **Encoder is shared, not owned**: x402 calls modsearch's `POST /embed`
  (:51090) so the box keeps one model in RAM. If modsearch is down, x402
  loads the model in-process (`X402_LOCAL_ENCODER=0` forbids that). If that
  fails too, `find` answers from FTS alone and returns `mode: "lexical"`.
- **Incremental**: rows are keyed by a hash of their text, so the refresh
  after each crawl only encodes what changed. A cold build embeds MCP tools
  first, then the most-used services, and searches the partial matrix
  (`mode: "partial"`) while the rest is embedded.
- Console: the **MCP SERVERS** tab, plus a words/meaning switch on SERVICES.
- MCP: the `x402_mcp` / `x402_find` tools on `/x402/mcp`.

## Agents: MCP, paying, trust

Any agent can find **and use** every service in the index over MCP. One
JSON-RPC engine (`x402mcp.py`) sits behind both transports:

```
claude mcp add --transport http x402 http://localhost:51110/mcp      # this box = owner
claude mcp add --transport http x402 https://modc2.com/x402/mcp \
    --header "Authorization: Bearer x402_..."                         # anyone else
claude mcp add x402 -- python3 /root/mod/mod/orbit/x402/x402mcp.py   # stdio, owner
```

Tools: `x402_search` `x402_find` `x402_mcp` `x402_service` `x402_providers`
`x402_stats` `x402_facilitators` (reads) · `x402_quote` `x402_call`
`x402_probe` (use) · `x402_whoami` `x402_trust` `x402_users` `x402_register`
`x402_vouch` `x402_revoke` (standing) · `x402_policy` `x402_set_trust`
`x402_wallet` `x402_sync` (owner). Each tool carries `_meta.min_score`,
`_meta.cost` and, in `tools/list`, whether *you* may call it.

**Paying** (`x402pay.py`). `x402_call` makes the request unpaid first. On a
402 there are three ways to pay, and the first one needs no trust in this
node at all:

1. **Your own wallet.** `x402_quote` returns EIP-712 typed data for an
   EIP-3009 `transferWithAuthorization` (exact amount, the offer's `payTo`,
   expiring with the offer). Sign it (`eth_signTypedData_v4`), then call
   `x402_call quote_id=… signature=0x…`. The node checks the signer and
   replays the identical request with `PAYMENT-SIGNATURE` (v2) or
   `X-PAYMENT` (v1). Quotes are single-use and bound to the caller.
2. **A finished payload.** `payment=<base64>` is relayed untouched, so any
   scheme and any chain works.
3. **The house wallet.** `~/.mod/x402/wallet.json` is off by default
   (`m x402/wallet create=1`, then `m x402/policy house=true`). It pays only
   for trusted callers who have made at least one self-paid call. Each call
   is capped by `max_call_usd`, each tier by `house_daily_usd`, and the whole
   node by `global_daily_usd`.

The node signs only `exact` offers on EVM chains with EIP-3009 tokens. Other
offers come back marked `signable:false`, and you can still pay them with
(2). A paid call that is still answered with 402 returns the facilitator's
reason in `payment_error`.

**Who is calling** (`x402trust.py`). The strongest proof wins:

| id | proof |
|---|---|
| `local` | stdio, the CLI, or loopback with no forwarding header. Owner standing. |
| `0x…` | a mod-protocol token in `token`, `x-mod-token` or `Authorization: Bearer`, signed EIP-191 or raw keccak, at most 7 days old |
| `key:…` | an API key from `x402_register`. It's shown once and only its hash is kept. |
| `anon:…` | nothing: a hash of IP + user-agent, capped at 19 |

**Trust 0-100** is recomputed from the ledger on every read and never
stored, so changing the formula re-scores everyone:

| part | how |
|---|---|
| base | wallet 30, key 20, anon 5 |
| tenure | `5·log2(1+days)`, up to 15 |
| activity | 2.5 per distinct day with a successful call (reads and unpaid quotes don't count), up to 15 within 90 days |
| settled | `8·log2(1+n)`, up to 25. Counts only self-paid calls where the facilitator returned a receipt with a transaction, to a host that some facilitator lists, so nobody can farm it against their own fake 402 server. |
| vouches | other callers' 1-10 ratings, weighted by the voucher's own score (at least 50, computed without vouches, so rings can't lift themselves). ±15 in total. |
| penalties | −2 per throttle and −5 per denied request (non-public URL), over 30 days |
| owner | `adjust`, `pin` or `ban` (`m x402/set_trust user=… pin=60`) |

| tier | from | may |
|---|---|---|
| restricted | 0 | read, register |
| basic | 20 | + quote and call hosts that are in the index |
| trusted | 40 | + call any public URL, probe, vouch, house budget |
| core | 70 | the same, with a bigger house budget and rate |
| owner | `local` / `policy.owners` | + set_trust, policy, wallet, sync, local URLs |

Rate limits per minute are restricted 20, basic 60, trusted 120 and core 300.
A refusal for missing standing costs nothing. State lives in `~/.mod/x402/`
(`agents.db`, `policy.json`, `wallet.json`, all private and off the repo).
The REST faces (`POST /x402/api/{call,quote,register,…}`, `GET users`,
`trust`, `whoami`, `tools`) run as the caller, never as the box.

## Where services come from

| source | how | finds |
|---|---|---|
| **facilitators** | the spec's discovery API, `GET {base}/discovery/resources?limit&offset`, paged to the end | Coinbase CDP Bazaar, PayAI, Dexter, GoPlausible, Ultravioleta DAO, ... |
| **ecosystem registry** | `coinbase/x402` → `typescript/site/app/ecosystem/partners-data/*/metadata.json` | about 200 projects, including every facilitator's `baseUrl` |
| **probe** | one unpaid call; a 402 carrying `accepts` (body for v1, base64 `PAYMENT-REQUIRED` header for v2) | anything, including services that no directory lists |

Facilitators aren't hard-coded. Only CDP and PayAI are seeded; every other
facilitator comes from the registry. Each one is asked once a day whether it
lists, and only those that do get crawled. When a new facilitator joins the
registry, it shows up here without code changes.

## How the merge works

- A service is keyed by its canonical URL: lower-case host, no trailing
  slash, no fragment.
- When several sources list the same URL, the richer field wins: an empty
  name never overwrites a real one, and call counts keep the max.
- Every (url, source) pair is a *sighting*. A crawl that **completes**
  retires the sightings it didn't renew. A service no source lists any more
  leaves the index. A partial crawl retires nothing.
- A probed service is *pinned*. That's first-hand evidence, so directories
  can't retire it.
- A price is only turned into USD when the asset is a known dollar
  stablecoin (by address, or by name such as "USD Coin" or "USDT0"). Any
  other asset keeps its raw amount and gets no USD price, because a wrong
  price is worse than none.
- Networks are shown by short name (`eip155:8453` → `base`); unknown CAIP-2
  ids pass through unchanged.

## Self-sustaining

- The server re-crawls when the index is older than `sync_every` (6h).
  Registry discovery refreshes daily.
- It's stdlib Python only: `urllib`, `sqlite3` with FTS5, and `http.server`.
  There are no keys, no SDK and no account.
- Everything lives in `data/x402.db` (WAL). Copy it, query it with
  `sqlite3`, or delete it to start over.
- Reads use per-thread connections, so the console stays responsive during a
  crawl.

## Access

Reads are public. The writes (`sync`, `discover`, `add_source`,
`remove_source`, `probe`, `forget`) are POST-only and **local-only**: a
request that came through the gateway (with `X-Forwarded-For` set) is refused
with 403. The reason is that probes make this box fetch someone else's URL,
and crawls spend its bandwidth. Set `X402_OPEN=1` to lift the restriction.
Set `X402_AUTOSYNC=0` to turn off the timer.

## Files

- `x402sem.py`: the semantic index, MCP-server folding and the encoder fallback chain.
- `x402src.py` holds the sources, transport and normalization (v1, v2, Bazaar extensions, GoPlausible).
- `x402db.py` holds the SQLite store: services, FTS, sightings, sources, partners.
- `x402mcp.py` is the MCP engine: the tool table, trust gates, quotes, and the stdio main.
- `x402pay.py` handles paying: SSRF guard, offers, EIP-3009 typed data, payloads, receipts, house wallet.
- `x402trust.py` holds identity, the ledger, the trust score, tiers, policy and rate limits.
- `mod.py` is the anchor (`Mod`); CLI and API both dispatch into it.
- `serve.py` serves the console and API on one port, and runs the autosync thread.
- `web/index.html` is the console: SERVICES (words/meaning), MCP SERVERS, PROVIDERS, FACILITATORS, ECOSYSTEM.
- `tests/` runs offline, with every fetch stubbed.
- `contracts/` and `x402/` are leftovers from the earlier module and aren't used.
