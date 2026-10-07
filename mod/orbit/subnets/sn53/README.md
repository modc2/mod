# sn53 — engy ب

Verified inference for frontier open models.

Bittensor subnet **53** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-07 (block 9229089).

Links: [github](https://github.com/hanlinai/engy) · [url](https://engy.ai)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.036840 | +0.87% | +8.35% | +8.15% | 227,118 | 39,608 | 17,160 |

## Last 24h flow

803 trades by 288 coldkeys · 417 buys (8,699 τ) / 386 sells (7,389 τ) · net 1,310 τ

## News

- 2026-09-29 · commit · [Merge pull request #51 from hanlinai/docs/miner-provider-docs-pointer](https://github.com/hanlinai/engy/commit/647c767e5994c1b041a014c51e76b0cfbc3cc936) — hanlinai/engy
- 2026-09-29 · commit · [docs: point MINER.md at the provider onboarding guide](https://github.com/hanlinai/engy/commit/5087bf45262373f3f21ddc7aa7181ba0a6e37d4d) — hanlinai/engy
- 2026-09-29 · commit · [Merge pull request #50 from hanlinai/docs/miner-lb-endpoint](https://github.com/hanlinai/engy/commit/d96784d7aa8c336f624b5938aad5ac64b0bcd0db) — hanlinai/engy
- 2026-09-29 · commit · [docs: recommend wss://lb.engy.ai/gw as the miner endpoint](https://github.com/hanlinai/engy/commit/ba18a8aa3b4f0eaa3d0dc374f5f12b4f4a1833ef) — hanlinai/engy
- 2026-09-13 · commit · [Merge pull request #45 from hanlinai/fix/tee-miner-kv-pool-retry](https://github.com/hanlinai/engy/commit/9dc14b4ac2aeb66a35c430e5bca650ceae08a6ff) — hanlinai/engy

## Use

```bash
m subnets.sn53/info        # live identity + market (snapshot if bt is down)
m subnets.sn53/news        # scraped news
m subnets.sn53/trades      # 24h alpha tape
m subnets.sn53/daily       # daily candles
python3 orbit/subnets/sn53/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
