# sn22 — Desearch χ

Decentralized search engine

Bittensor subnet **22** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-20 (block 9322663).

Links: [github](https://github.com/datura-ai/desearch) · [url](https://desearch.ai) · [discord](https://discord.gg/P44zrJmdFy)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002955 | -0.00% | +1.02% | -5.45% | 18,622 | 7,461 | 235.09 |

## Last 24h flow

112 trades by 21 coldkeys · 14 buys (135.84 τ) / 98 sells (97.59 τ) · net 38.25 τ

## News

- 2026-10-19 · commit · [Merge pull request #392 from Desearch-ai/develop](https://github.com/Desearch-ai/subnet-22/commit/8f3e0a9a8b30e315d02311f026493026ee7ab2ca) — datura-ai/desearch
- 2026-10-19 · commit · [chore: update version to 0.0.238](https://github.com/Desearch-ai/subnet-22/commit/570a83b0ec4d0d6b60d339e44f6e5fe5caab067d) — datura-ai/desearch
- 2026-10-19 · commit · [fix(validators): embed checks wait up to 120 s for reference vectors,…](https://github.com/Desearch-ai/subnet-22/commit/97aa8478d5d17f803ca5959cf5e22905d3923c6e) — datura-ai/desearch
- 2026-10-19 · commit · [docs: completed uploads are read in the version their completion pinn…](https://github.com/Desearch-ai/subnet-22/commit/664eb1ce3c5c36c92016d6ba1d8bb09a7dfc12aa) — datura-ai/desearch
- 2026-10-19 · commit · [feat(publisher): uploads are read only in the version their completio…](https://github.com/Desearch-ai/subnet-22/commit/115b92773b40923315aa3dfc9a8311d360edb2f6) — datura-ai/desearch
- 2026-10-19 · commit · [Merge pull request #391 from Desearch-ai/develop](https://github.com/Desearch-ai/subnet-22/commit/9b1300f7e799985f156873796446a3a9e04c0ab1) — datura-ai/desearch
- 2026-10-19 · commit · [chore: update version to 0.0.237](https://github.com/Desearch-ai/subnet-22/commit/683025511db51c687ed9228edb9ac04b17c412ac) — datura-ai/desearch
- 2026-10-19 · commit · [fix(task-api): reference vectors finish and stay kept even when the a…](https://github.com/Desearch-ai/subnet-22/commit/3d9fa1fa30b2610bd60e184113150920a1c5bbd7) — datura-ai/desearch

## Use

```bash
m subnets.sn22/info        # live identity + market (snapshot if bt is down)
m subnets.sn22/news        # scraped news
m subnets.sn22/trades      # 24h alpha tape
m subnets.sn22/daily       # daily candles
python3 orbit/subnets/sn22/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
