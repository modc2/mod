# sn22 — Desearch χ

Decentralized search engine

Bittensor subnet **22** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-02 (block 9195968).

Links: [github](https://github.com/datura-ai/desearch) · [url](https://desearch.ai) · [discord](https://discord.gg/P44zrJmdFy)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002947 | -0.26% | -0.23% | -1.45% | 18,207 | 7,450 | 498.26 |

## Last 24h flow

64 trades by 21 coldkeys · 24 buys (244.34 τ) / 40 sells (252.90 τ) · net -8.56 τ

## News

- 2026-10-01 · commit · [Merge pull request #380 from Desearch-ai/develop](https://github.com/Desearch-ai/subnet-22/commit/a88c51d510b9f69186ee221dac0c1ae7be54e29f) — datura-ai/desearch
- 2026-10-01 · commit · [chore: update version to 0.0.232](https://github.com/Desearch-ai/subnet-22/commit/7845cb041b2b647ed78a59c4f7bead01d27de0e4) — datura-ai/desearch
- 2026-10-01 · commit · [fix(validators): take an upload before waiting on its seed, so parall…](https://github.com/Desearch-ai/subnet-22/commit/fbf05aa791c323b9b87e7ef3c73bd140a29ded0f) — datura-ai/desearch
- 2026-10-01 · commit · [Merge pull request #379 from Desearch-ai/develop](https://github.com/Desearch-ai/subnet-22/commit/e923cf6ba3592ba0543b21d78fa1e20dac050427) — datura-ai/desearch
- 2026-10-01 · commit · [chore: update version to 0.0.231](https://github.com/Desearch-ai/subnet-22/commit/25af053b349ff1f0196a7eb75e29de493a727e9b) — datura-ai/desearch

## Use

```bash
m subnets.sn22/info        # live identity + market (snapshot if bt is down)
m subnets.sn22/news        # scraped news
m subnets.sn22/trades      # 24h alpha tape
m subnets.sn22/daily       # daily candles
python3 orbit/subnets/sn22/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
