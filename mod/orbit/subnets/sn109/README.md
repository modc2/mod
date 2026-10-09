# sn109 — Finsight ՞

Finsight is a decentralized personal investment intelligence. Turn the financial news, filings, and market data that touch an investor's holdings into scored, portfolio-aware signals. Bittensor powers finsight.tech, a Canada-first portfolio platform with native TFSA/RRSP logic, CRA-ready cost-basis tracking, and superficial-loss awareness. Built for Canadians by Canadians.

Bittensor subnet **109** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-09 (block 9243466).

Links: [url](https://finsight.tech/)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002821 | -0.00% | -1.46% | +16.37% | 6,464 | 1,389 | 38.20 |

## Last 24h flow

19 trades by 12 coldkeys · 9 buys (13.97 τ) / 10 sells (23.41 τ) · net -9.44 τ

## Use

```bash
m subnets.sn109/info        # live identity + market (snapshot if bt is down)
m subnets.sn109/news        # scraped news
m subnets.sn109/trades      # 24h alpha tape
m subnets.sn109/daily       # daily candles
python3 orbit/subnets/sn109/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
