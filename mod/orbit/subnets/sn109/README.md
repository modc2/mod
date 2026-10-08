# sn109 — Finsight ՞

Finsight is a decentralized personal investment intelligence. Turn the financial news, filings, and market data that touch an investor's holdings into scored, portfolio-aware signals. Bittensor powers finsight.tech, a Canada-first portfolio platform with native TFSA/RRSP logic, CRA-ready cost-basis tracking, and superficial-loss awareness. Built for Canadians by Canadians.

Bittensor subnet **109** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-08 (block 9236282).

Links: [url](https://finsight.tech/)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002863 | +1.43% | -7.25% | +23.04% | 6,540 | 1,399 | 313.98 |

## Last 24h flow

69 trades by 32 coldkeys · 34 buys (130.19 τ) / 35 sells (182.83 τ) · net -52.64 τ

## Use

```bash
m subnets.sn109/info        # live identity + market (snapshot if bt is down)
m subnets.sn109/news        # scraped news
m subnets.sn109/trades      # 24h alpha tape
m subnets.sn109/daily       # daily candles
python3 orbit/subnets/sn109/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
