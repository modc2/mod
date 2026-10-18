# sn88 — Investing ᛉ

Decentralized AUM

Bittensor subnet **88** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-18 (block 9308119).

Links: [github](https://github.com/mobiusfund/investing) · [url](https://Investing88.ai)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002642 | +0.00% | +0.18% | -6.47% | 14,490 | 5,773 | 92.99 |

## Last 24h flow

34 trades by 25 coldkeys · 12 buys (49.10 τ) / 22 sells (43.56 τ) · net 5.54 τ

## News

- 2026-09-24 · commit · [Incentivized conviction](https://github.com/mobiusfund/investing/commit/4859a5869fb63bcb986d85bbf6faabb0386cb244) — mobiusfund/investing
- 2026-09-24 · commit · [Misc updates](https://github.com/mobiusfund/investing/commit/86d63188bb20c1cd2b5671b0b4b90881273407d2) — mobiusfund/investing

## Use

```bash
m subnets.sn88/info        # live identity + market (snapshot if bt is down)
m subnets.sn88/news        # scraped news
m subnets.sn88/trades      # 24h alpha tape
m subnets.sn88/daily       # daily candles
python3 orbit/subnets/sn88/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
