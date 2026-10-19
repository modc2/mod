# Architecture

```
 browser ──► app (Next.js :50131, /openhouse) ──rewrite /api/*──► api (FastAPI :50132)
 agent   ──► POST /mcp  (same API) ───────────────────────────────┤
                                                                   ▼
                                                         mod.py  Mod  ──► ~/.openhouse/*.json
                                       bank/ · proofs.py · pq.py · simulate.py · peers.py · fx.py
                                                                   │
                                                  contracts/ (EVM) · near/ (WASM) · proof/ (Cairo)
```

## One brain

`Mod` in `mod.py` holds every rule. The REST API, the MCP server, the CLI (`m.mod('openhouse')().<fn>()`) and the simulator all call the same methods, so a number means the same thing everywhere. `config.json` `fns` is the public list (75 entries; `forward(action, ...)` dispatches any of them by name).

## The deal

- `terms` / `set_terms`: model, fee % (0-5, zero allowed), home price, monthly rent, owner, treasury. `models()` lists the rent-to-own models (e.g. `full_credit`).
- `quote` shows what one payment buys; `pay_rent` records it. The fee goes to the pool, principal accrues to the renter as equity, the rest is owner income. Equity is clamped so principal never exceeds the home price.
- Every 90 days `close_quarter` freezes the fee pool and splits it by **bloctime** (locked liquidity x time); `pool_claim` pays one address's share.
- `purchase` / `distribute` / `dividends` / `shareholders`: the older share-and-dividend accounting.
- Governance on-chain is a 2-of-2 owner+bank multisig (see [CONTRACTS](CONTRACTS.md)); locally `claim_owner`, `transfer_authority`, `toggle_active` and `record_action` keep the bookkeeping.

## Storage

Live store: `~/.openhouse/{shareholders,properties,dividends,...}.json`. Bank credentials default to `~/.mod/openhouse/`. Simulations and examples use a throwaway in-memory or temp store and never touch the live one. `demo.py` can seed (`seed`) and remove (`unseed`) a labelled fake property in the live store.

## Side rails

- `peers.py`: 12 competitor projects sorted by who ends up owning the house. Editorial facts carry source URLs; unknown values are `None` and render "-". Live numbers come from RealT and CoinGecko, cached 15 min in `~/.openhouse/peers_cache.json`.
- `fx.py`: ETH in 12 fiat currencies from CoinGecko, `source` is `live | cache | stale | fallback` (fallback means approximate). Cache `~/.openhouse/fx_cache.json`.

## Conventions and traps

- `mod.py` loads siblings by file path (`importlib.util.spec_from_file_location`), not by name - `peers`, `fx` and `mod` are too generic for `sys.path`.
- The MCP file is `mcp_server.py`, never `mcp.py`: uvicorn puts `api/` on `sys.path` and a local `mcp.py` would shadow the real `mcp` package.
- Every mod ships a `mod.py`, so scripts and tests put `/root/mod` on `sys.path` explicitly (`import mod as m` must resolve to the protocol package).
- Ports live in `config.json`, `src/app/next.config.js`, `docker-entrypoint.sh`, `api/api.py`, `mod.py` and the MCP registration in `/root/.claude.json`. Change them together.
- `next.config.js` uses `output: 'standalone'`, which breaks `next start`; serve with `next dev` or `node .next/standalone/server.js`.
