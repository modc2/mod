# sn41 — Almanac נ

Prediction market research and intelligence.

Bittensor subnet **41** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-06 (block 9221879).

Links: [github](https://github.com/corvxai/almanac) · [url](https://almnc.ai)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.007383 | +0.13% | -1.00% | -5.17% | 43,555 | 15,356 | 1,646 |

## Last 24h flow

218 trades by 150 coldkeys · 60 buys (775.28 τ) / 158 sells (858.52 τ) · net -83.24 τ

## News

- 2026-09-30 · commit · [Merge pull request #54 from corvxai/remove_gen_pool_logging](https://github.com/corvxai/almanac/commit/31a4378cb4f9ba49c957f0790cfd16ba4cf284b4) — corvxai/almanac
- 2026-09-30 · commit · [Merge pull request #53 from corvxai/burn_updates](https://github.com/corvxai/almanac/commit/228278c8f4528b1565bd2b006126433defc9e609) — corvxai/almanac
- 2026-09-30 · commit · [If general pool trading is disabled, don't print the tables in the logs](https://github.com/corvxai/almanac/commit/523d459f5ece4038b3b7a930f9d722941ce5f23a) — corvxai/almanac
- 2026-09-30 · commit · [Updates to handling excess miner emissions and burn](https://github.com/corvxai/almanac/commit/281009a43dc4a9c72d6f2a8657b0c5fca9d6ead1) — corvxai/almanac
- 2026-09-30 · commit · [Merge pull request #52 from corvxai/hotfix/forecast_miner_check](https://github.com/corvxai/almanac/commit/69ebadcff5cb17bd1fd8e443f145fe56b8d992ad) — corvxai/almanac

## Use

```bash
m subnets.sn41/info        # live identity + market (snapshot if bt is down)
m subnets.sn41/news        # scraped news
m subnets.sn41/trades      # 24h alpha tape
m subnets.sn41/daily       # daily candles
python3 orbit/subnets/sn41/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
