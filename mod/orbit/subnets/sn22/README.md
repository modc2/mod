# sn22 — Desearch χ

Decentralized search engine

Bittensor subnet **22** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-19 (block 9314686).

Links: [github](https://github.com/datura-ai/desearch) · [url](https://desearch.ai) · [discord](https://discord.gg/P44zrJmdFy)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002925 | — | -0.48% | -6.74% | 18,412 | 7,423 | 21.31 |

## Last 24h flow

167 trades by 20 coldkeys · 6 buys (1.43 τ) / 161 sells (19.34 τ) · net -17.91 τ

## News

- 2026-10-11 · commit · [Merge pull request #390 from Desearch-ai/hotfix/no-render](https://github.com/Desearch-ai/subnet-22/commit/eb56bb5e7e68f884824f2cbefbfd05005302f2ed) — datura-ai/desearch
- 2026-10-11 · commit · [fix(validators): no JS rendering, 20 pages per 1,000-URL task, fail o…](https://github.com/Desearch-ai/subnet-22/commit/0098ed262fc5c476567ef890e36b1a247f327a0e) — datura-ai/desearch
- 2026-10-11 · commit · [chore: update version to 0.0.236](https://github.com/Desearch-ai/subnet-22/commit/7a535d2796a77355dba42400cf4dcd2f6af1ce7c) — datura-ai/desearch
- 2026-10-11 · commit · [Merge pull request #388 from Desearch-ai/hotfix/restore-sampling](https://github.com/Desearch-ai/subnet-22/commit/c3816958e55b2afc59bfe9c2aaafb60da07105fb) — datura-ai/desearch
- 2026-10-11 · commit · [fix(validators): restore rendered rechecks and the 5% sample](https://github.com/Desearch-ai/subnet-22/commit/593cd20f451e68ed5341dfae61e986c7e63886dd) — datura-ai/desearch
- 2026-10-11 · commit · [chore: update version to 0.0.235](https://github.com/Desearch-ai/subnet-22/commit/5131df14568c43c740fb38bb2126069343c8483b) — datura-ai/desearch
- 2026-10-11 · commit · [Merge pull request #387 from Desearch-ai/hotfix/validator-provider](https://github.com/Desearch-ai/subnet-22/commit/7b398ba1580c81003e84701715215f99a10c2138) — datura-ai/desearch
- 2026-10-11 · commit · [chore: update version to 0.0.234](https://github.com/Desearch-ai/subnet-22/commit/3cf4417defa00ba771fc022ae5230bba2a5b0d0c) — datura-ai/desearch

## Use

```bash
m subnets.sn22/info        # live identity + market (snapshot if bt is down)
m subnets.sn22/news        # scraped news
m subnets.sn22/trades      # 24h alpha tape
m subnets.sn22/daily       # daily candles
python3 orbit/subnets/sn22/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
