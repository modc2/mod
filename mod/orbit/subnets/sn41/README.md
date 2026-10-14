# sn41 — Almanac נ

Prediction market research and intelligence.

Bittensor subnet **41** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-14 (block 9279460).

Links: [github](https://github.com/corvxai/almanac) · [url](https://almnc.ai)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.007056 | +0.00% | -3.03% | -2.56% | 42,085 | 15,070 | 2,032 |

## Last 24h flow

191 trades by 119 coldkeys · 66 buys (891.77 τ) / 125 sells (1,128 τ) · net -235.80 τ

## News

- 2026-10-13 · commit · [Merge pull request #56 from corvxai/version_logging](https://github.com/corvxai/almanac/commit/5e7113f8fb8e5cb51322f811367b4a21849108c4) — corvxai/almanac
- 2026-10-13 · commit · [Adding weights version key to validator logs as a helpful indicator t…](https://github.com/corvxai/almanac/commit/fd001cdbeac494d5a5f61822740dc2f06fa17ce0) — corvxai/almanac
- 2026-10-08 · commit · [Merge pull request #55 from corvxai/forecasting_scoring_v2](https://github.com/corvxai/almanac/commit/a3a5091708cc965c80de1cdfa1f040ad21555dfc) — corvxai/almanac
- 2026-10-07 · commit · [Proper scaling](https://github.com/corvxai/almanac/commit/52c60e6f14dae96844acf79a240b01221802f4a2) — corvxai/almanac
- 2026-10-07 · commit · [Fixing audit items for forecast scoring. minimum predictions to be sk…](https://github.com/corvxai/almanac/commit/24171459991eb929d2e1c44d112e0499d70c6098) — corvxai/almanac
- 2026-10-06 · commit · [Fixing comment](https://github.com/corvxai/almanac/commit/7e2a78ca0f554c3fa04e9d26a86f4816c6c4a672) — corvxai/almanac
- 2026-10-06 · commit · [Updating forecast scoring to a v2. Drops the calibration pillar and i…](https://github.com/corvxai/almanac/commit/4c9f942136d90a93351120b352e7b68febeb7fd2) — corvxai/almanac
- 2026-09-30 · commit · [Merge pull request #54 from corvxai/remove_gen_pool_logging](https://github.com/corvxai/almanac/commit/31a4378cb4f9ba49c957f0790cfd16ba4cf284b4) — corvxai/almanac

## Use

```bash
m subnets.sn41/info        # live identity + market (snapshot if bt is down)
m subnets.sn41/news        # scraped news
m subnets.sn41/trades      # 24h alpha tape
m subnets.sn41/daily       # daily candles
python3 orbit/subnets/sn41/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
