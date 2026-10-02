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

- `x402src.py` holds the sources, transport and normalization (v1, v2, Bazaar extensions, GoPlausible).
- `x402db.py` holds the SQLite store: services, FTS, sightings, sources, partners.
- `mod.py` is the anchor (`Mod`); CLI and API both dispatch into it.
- `serve.py` serves the console and API on one port, and runs the autosync thread.
- `web/index.html` is the console: SERVICES, PROVIDERS, FACILITATORS, ECOSYSTEM.
- `tests/` runs offline, with every fetch stubbed.
- `contracts/` and `x402/` are leftovers from the earlier module and aren't used.
