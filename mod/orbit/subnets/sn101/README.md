# sn101 — Tag101 ე

Tag101 is a Bittensor subnet for decentralized social post tagging

Bittensor subnet **101** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-16 (block 9293868).

Links: [github](https://github.com/tag101-ai/tag101) · [url](http://tag101.ai) · discord `Tag101`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003622 | +0.01% | -0.20% | -1.78% | 13,145 | 3,552 | 78.71 |

## Last 24h flow

152 trades by 16 coldkeys · 7 buys (35.23 τ) / 145 sells (40.59 τ) · net -5.36 τ

## News

- 2026-10-14 · commit · [Merge pull request #10 from tag101-ai/dev](https://github.com/tag101-ai/tag101/commit/c3d788bfd7a525e63d06caaa3954947eed7cd923) — tag101-ai/tag101
- 2026-10-14 · commit · [Pin the reprocessed tweet corpus and add 1.1.1 release metadata](https://github.com/tag101-ai/tag101/commit/7154210011a0b3981ca5659b20724c02e1c4ac8a) — tag101-ai/tag101
- 2026-10-09 · commit · [Merge pull request #9 from tag101-ai/dev](https://github.com/tag101-ai/tag101/commit/6571c0bfa901a0c022101a45796c3059fb87e2e8) — tag101-ai/tag101
- 2026-10-08 · commit · [Add v1.1 scoring, AWS corpus leasing, weight-setting hardening, and l…](https://github.com/tag101-ai/tag101/commit/67b1a358a74452ab57f18b7bf02f26443709f4d7) — tag101-ai/tag101

## Use

```bash
m subnets.sn101/info        # live identity + market (snapshot if bt is down)
m subnets.sn101/news        # scraped news
m subnets.sn101/trades      # 24h alpha tape
m subnets.sn101/daily       # daily candles
python3 orbit/subnets/sn101/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
