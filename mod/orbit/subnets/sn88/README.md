# sn88 — Investing ᛉ

Decentralized AUM

Bittensor subnet **88** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-17 (block 9300804).

Links: [github](https://github.com/mobiusfund/investing) · [url](https://Investing88.ai)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002652 | +0.00% | +0.10% | -6.40% | 14,529 | 5,784 | 26.81 |

## Last 24h flow

23 trades by 18 coldkeys · 6 buys (14.66 τ) / 17 sells (7.13 τ) · net 7.52 τ

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
