# sn41 — Almanac נ

Prediction market research and intelligence.

Bittensor subnet **41** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-15 (block 9286668).

Links: [github](https://github.com/corvxai/almanac) · [url](https://almnc.ai)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.006903 | +0.00% | -2.17% | -11.53% | 41,230 | 14,913 | 1,893 |

## Last 24h flow

174 trades by 105 coldkeys · 67 buys (856.80 τ) / 107 sells (1,027 τ) · net -170.46 τ

## News

- 2026-10-14 · commit · [Merge pull request #57 from corvxai/effn-gate-at-last-resolution](https://github.com/corvxai/almanac/commit/df48ccf6ced8c4b29cc61e98adbf6cef89162149) — corvxai/almanac
- 2026-10-14 · commit · [Upping weights key version to 3](https://github.com/corvxai/almanac/commit/30c865aff31f9bc3abf63c3fdf7c11b088567e99) — corvxai/almanac
- 2026-10-13 · commit · [Score update](https://github.com/corvxai/almanac/commit/ee4f5dda9c445fc9c1e1450b2d01702d890a0fc4) — corvxai/almanac
- 2026-10-13 · commit · [Merge pull request #56 from corvxai/version_logging](https://github.com/corvxai/almanac/commit/5e7113f8fb8e5cb51322f811367b4a21849108c4) — corvxai/almanac
- 2026-10-13 · commit · [Adding weights version key to validator logs as a helpful indicator t…](https://github.com/corvxai/almanac/commit/fd001cdbeac494d5a5f61822740dc2f06fa17ce0) — corvxai/almanac
- 2026-10-08 · commit · [Merge pull request #55 from corvxai/forecasting_scoring_v2](https://github.com/corvxai/almanac/commit/a3a5091708cc965c80de1cdfa1f040ad21555dfc) — corvxai/almanac
- 2026-10-07 · commit · [Proper scaling](https://github.com/corvxai/almanac/commit/52c60e6f14dae96844acf79a240b01221802f4a2) — corvxai/almanac
- 2026-10-07 · commit · [Fixing audit items for forecast scoring. minimum predictions to be sk…](https://github.com/corvxai/almanac/commit/24171459991eb929d2e1c44d112e0499d70c6098) — corvxai/almanac

## Use

```bash
m subnets.sn41/info        # live identity + market (snapshot if bt is down)
m subnets.sn41/news        # scraped news
m subnets.sn41/trades      # 24h alpha tape
m subnets.sn41/daily       # daily candles
python3 orbit/subnets/sn41/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
