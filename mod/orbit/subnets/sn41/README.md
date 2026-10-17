# sn41 — Almanac נ

Prediction market research and intelligence.

Bittensor subnet **41** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-17 (block 9300804).

Links: [github](https://github.com/corvxai/almanac) · [url](https://almnc.ai)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.006839 | +0.00% | -5.19% | -6.20% | 40,959 | 14,858 | 1,293 |

## Last 24h flow

192 trades by 130 coldkeys · 53 buys (439.12 τ) / 139 sells (844.63 τ) · net -405.52 τ

## News

- 2026-10-15 · commit · [Merge pull request #58 from corvxai/vali_stale_weights_etc](https://github.com/corvxai/almanac/commit/60111f58ed4d4312b2b85a871c49197f01f02c57) — corvxai/almanac
- 2026-10-15 · commit · [Upping weights_version_key to 4](https://github.com/corvxai/almanac/commit/c8f41159cac7f64b90eeb3e82c8dc3b9da1e3902) — corvxai/almanac
- 2026-10-15 · commit · [Adding retries, logging, better timeouts, increased limit of 200 to f…](https://github.com/corvxai/almanac/commit/b59e179e84551a5da910d3bc4ef7ed496bb27863) — corvxai/almanac
- 2026-10-14 · commit · [Merge pull request #57 from corvxai/effn-gate-at-last-resolution](https://github.com/corvxai/almanac/commit/df48ccf6ced8c4b29cc61e98adbf6cef89162149) — corvxai/almanac
- 2026-10-14 · commit · [Upping weights key version to 3](https://github.com/corvxai/almanac/commit/30c865aff31f9bc3abf63c3fdf7c11b088567e99) — corvxai/almanac
- 2026-10-13 · commit · [Score update](https://github.com/corvxai/almanac/commit/ee4f5dda9c445fc9c1e1450b2d01702d890a0fc4) — corvxai/almanac
- 2026-10-13 · commit · [Merge pull request #56 from corvxai/version_logging](https://github.com/corvxai/almanac/commit/5e7113f8fb8e5cb51322f811367b4a21849108c4) — corvxai/almanac
- 2026-10-13 · commit · [Adding weights version key to validator logs as a helpful indicator t…](https://github.com/corvxai/almanac/commit/fd001cdbeac494d5a5f61822740dc2f06fa17ce0) — corvxai/almanac

## Use

```bash
m subnets.sn41/info        # live identity + market (snapshot if bt is down)
m subnets.sn41/news        # scraped news
m subnets.sn41/trades      # 24h alpha tape
m subnets.sn41/daily       # daily candles
python3 orbit/subnets/sn41/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
