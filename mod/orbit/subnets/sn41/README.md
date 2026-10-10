# sn41 — Almanac נ

Prediction market research and intelligence.

Bittensor subnet **41** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-10 (block 9250674).

Links: [github](https://github.com/corvxai/almanac) · [url](https://almnc.ai)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.007291 | -0.10% | -3.67% | -7.25% | 43,252 | 15,290 | 702.79 |

## Last 24h flow

152 trades by 133 coldkeys · 20 buys (198.34 τ) / 132 sells (494.04 τ) · net -295.70 τ

## News

- 2026-10-08 · commit · [Merge pull request #55 from corvxai/forecasting_scoring_v2](https://github.com/corvxai/almanac/commit/a3a5091708cc965c80de1cdfa1f040ad21555dfc) — corvxai/almanac
- 2026-10-07 · commit · [Proper scaling](https://github.com/corvxai/almanac/commit/52c60e6f14dae96844acf79a240b01221802f4a2) — corvxai/almanac
- 2026-10-07 · commit · [Fixing audit items for forecast scoring. minimum predictions to be sk…](https://github.com/corvxai/almanac/commit/24171459991eb929d2e1c44d112e0499d70c6098) — corvxai/almanac
- 2026-10-06 · commit · [Fixing comment](https://github.com/corvxai/almanac/commit/7e2a78ca0f554c3fa04e9d26a86f4816c6c4a672) — corvxai/almanac
- 2026-10-06 · commit · [Updating forecast scoring to a v2. Drops the calibration pillar and i…](https://github.com/corvxai/almanac/commit/4c9f942136d90a93351120b352e7b68febeb7fd2) — corvxai/almanac
- 2026-09-30 · commit · [Merge pull request #54 from corvxai/remove_gen_pool_logging](https://github.com/corvxai/almanac/commit/31a4378cb4f9ba49c957f0790cfd16ba4cf284b4) — corvxai/almanac
- 2026-09-30 · commit · [Merge pull request #53 from corvxai/burn_updates](https://github.com/corvxai/almanac/commit/228278c8f4528b1565bd2b006126433defc9e609) — corvxai/almanac
- 2026-09-30 · commit · [If general pool trading is disabled, don't print the tables in the logs](https://github.com/corvxai/almanac/commit/523d459f5ece4038b3b7a930f9d722941ce5f23a) — corvxai/almanac

## Use

```bash
m subnets.sn41/info        # live identity + market (snapshot if bt is down)
m subnets.sn41/news        # scraped news
m subnets.sn41/trades      # 24h alpha tape
m subnets.sn41/daily       # daily candles
python3 orbit/subnets/sn41/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
