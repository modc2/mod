# sn41 — Almanac נ

Prediction market research and intelligence.

Bittensor subnet **41** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-19 (block 9314686).

Links: [github](https://github.com/corvxai/almanac) · [url](https://almnc.ai)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.006653 | — | -0.76% | -9.45% | 39,947 | 14,667 | 475.95 |

## Last 24h flow

82 trades by 60 coldkeys · 27 buys (205.15 τ) / 55 sells (264.55 τ) · net -59.40 τ

## News

- 2026-10-17 · commit · [Merge pull request #60 from corvxai/wvk_bump](https://github.com/corvxai/almanac/commit/935764a96fa8d3c4ea99e03720d3f5d7a2bfa7c0) — corvxai/almanac
- 2026-10-17 · commit · [Upping key to 5](https://github.com/corvxai/almanac/commit/4174fe2a6e842775aad4fa8f48cc0419ce2d6526) — corvxai/almanac
- 2026-10-17 · commit · [Merge pull request #59 from corvxai/vali_no_assign_before_scoring](https://github.com/corvxai/almanac/commit/977b909986bfae2d0a07a69b0b8061098aa31cb4) — corvxai/almanac
- 2026-10-17 · commit · [Agent and event assignment and execution will not execute within 5 mi…](https://github.com/corvxai/almanac/commit/8c391c6773263ab58b64470ccb347a4b2b0ad305) — corvxai/almanac
- 2026-10-15 · commit · [Merge pull request #58 from corvxai/vali_stale_weights_etc](https://github.com/corvxai/almanac/commit/60111f58ed4d4312b2b85a871c49197f01f02c57) — corvxai/almanac
- 2026-10-15 · commit · [Upping weights_version_key to 4](https://github.com/corvxai/almanac/commit/c8f41159cac7f64b90eeb3e82c8dc3b9da1e3902) — corvxai/almanac
- 2026-10-15 · commit · [Adding retries, logging, better timeouts, increased limit of 200 to f…](https://github.com/corvxai/almanac/commit/b59e179e84551a5da910d3bc4ef7ed496bb27863) — corvxai/almanac
- 2026-10-14 · commit · [Merge pull request #57 from corvxai/effn-gate-at-last-resolution](https://github.com/corvxai/almanac/commit/df48ccf6ced8c4b29cc61e98adbf6cef89162149) — corvxai/almanac

## Use

```bash
m subnets.sn41/info        # live identity + market (snapshot if bt is down)
m subnets.sn41/news        # scraped news
m subnets.sn41/trades      # 24h alpha tape
m subnets.sn41/daily       # daily candles
python3 orbit/subnets/sn41/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
