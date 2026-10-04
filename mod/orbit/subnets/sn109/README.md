# sn109 — Finsight ՞

Finsight is a decentralized personal investment intelligence. Turn the financial news, filings, and market data that touch an investor's holdings into scored, portfolio-aware signals. Bittensor powers finsight.tech, a Canada-first portfolio platform with native TFSA/RRSP logic, CRA-ready cost-basis tracking, and superficial-loss awareness. Built for Canadians by Canadians.

Bittensor subnet **109** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-04 (block 9207487).

Links: [url](https://finsight.tech/)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002517 | +0.00% | +2.40% | +4.65% | 5,677 | 1,312 | 16.93 |

## Last 24h flow

11 trades by 7 coldkeys · 7 buys (16.14 τ) / 4 sells (0.35 τ) · net 15.79 τ

## Use

```bash
m subnets.sn109/info        # live identity + market (snapshot if bt is down)
m subnets.sn109/news        # scraped news
m subnets.sn109/trades      # 24h alpha tape
m subnets.sn109/daily       # daily candles
python3 orbit/subnets/sn109/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
