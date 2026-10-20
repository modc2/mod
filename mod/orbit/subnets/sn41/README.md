# sn41 — Almanac נ

Prediction market research and intelligence.

Bittensor subnet **41** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-20 (block 9322663).

Links: [github](https://github.com/corvxai/almanac) · [url](https://almnc.ai)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.007690 | +0.32% | +14.33% | +5.68% | 46,242 | 15,777 | 8,006 |

## Last 24h flow

666 trades by 217 coldkeys · 328 buys (4,509 τ) / 338 sells (3,486 τ) · net 1,022 τ

## News

- 2026-10-19 · commit · [Merge pull request #62 from corvxai/etao_update](https://github.com/corvxai/almanac/commit/e1280c05e3ef3e081f40ace76047503e4d7d41a8) — corvxai/almanac
- 2026-10-19 · commit · [Updating market weight share back to 95% because overall pool is larg…](https://github.com/corvxai/almanac/commit/6edd093e8fb935806c1531156b5d82014b27da03) — corvxai/almanac
- 2026-10-19 · commit · [Updating default miner alpha to 3600, or 50% of total emissions with …](https://github.com/corvxai/almanac/commit/d11863b6325d3f3ca98795ce4dbf87b916f45495) — corvxai/almanac
- 2026-10-19 · commit · [Merge pull request #61 from corvxai/pm-data-v2](https://github.com/corvxai/almanac/commit/e1c643f3192fc07afdb0f85a840d9f8470d95a55) — corvxai/almanac
- 2026-10-19 · commit · [Migrating from PM's Data V1 to V2 API.](https://github.com/corvxai/almanac/commit/cf558ea3887233f2e272d4cf691882e381f7104b) — corvxai/almanac
- 2026-10-17 · commit · [Merge pull request #60 from corvxai/wvk_bump](https://github.com/corvxai/almanac/commit/935764a96fa8d3c4ea99e03720d3f5d7a2bfa7c0) — corvxai/almanac
- 2026-10-17 · commit · [Upping key to 5](https://github.com/corvxai/almanac/commit/4174fe2a6e842775aad4fa8f48cc0419ce2d6526) — corvxai/almanac
- 2026-10-17 · commit · [Merge pull request #59 from corvxai/vali_no_assign_before_scoring](https://github.com/corvxai/almanac/commit/977b909986bfae2d0a07a69b0b8061098aa31cb4) — corvxai/almanac

## Use

```bash
m subnets.sn41/info        # live identity + market (snapshot if bt is down)
m subnets.sn41/news        # scraped news
m subnets.sn41/trades      # 24h alpha tape
m subnets.sn41/daily       # daily candles
python3 orbit/subnets/sn41/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
