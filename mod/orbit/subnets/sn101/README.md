# sn101 — Tag101 ე

Tag101 is a Bittensor subnet for decentralized social post tagging

Bittensor subnet **101** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-11 (block 9257875).

Links: [github](https://github.com/tag101-ai/tag101) · [url](http://tag101.ai) · discord `Tag101`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003671 | +0.00% | -1.48% | -1.67% | 13,194 | 3,567 | 29.40 |

## Last 24h flow

19 trades by 8 coldkeys · 1 buys (0.00 τ) / 18 sells (27.64 τ) · net -27.64 τ

## News

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
