# sn127 — Astrid 𑀅

The capital axis for Bittensor.

Bittensor subnet **127** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-15 (block 9286668).

Links: [github](https://github.com/astridintelligence/sn-127) · [url](https://www.astrid.global/)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002189 | -0.00% | -2.03% | +0.61% | 9,640 | 2,942 | 32.30 |

## Last 24h flow

3 trades by 3 coldkeys · 1 buys (1.01 τ) / 2 sells (30.99 τ) · net -29.98 τ

## News

- 2026-10-01 · news · [Astrid Intelligence continues to expand its Bittensor platform](https://news.google.com/rss/articles/CBMimwFBVV95cUxPbmhTV3dPWl9MU1owc0s5Zk14WUlsUXBmd3Q1ZXEzNDNCN1NfMUVpb3N5ekJnaFZMTXMyY19ramUxeUh3NmdXSkszZmd0Nmd6MUNaUnJxTkU2Q0VUaGRJbGIyOVpaX1NHNWVHZmxiMklnMk9qMDNsNVhJUG9sNEhRQjl0YndXVG9Ed21TcFVWRmdublBqMkVCSzE5VQ?oc=5) — Yahoo Finance UK
- 2026-09-22 · commit · [Opening trades should also incur fees.](https://github.com/astridintelligence/sn-127/commit/4eb40ff280e3b61a7ba3decb9f03b2eda7281862) — astridintelligence/sn-127

## Use

```bash
m subnets.sn127/info        # live identity + market (snapshot if bt is down)
m subnets.sn127/news        # scraped news
m subnets.sn127/trades      # 24h alpha tape
m subnets.sn127/daily       # daily candles
python3 orbit/subnets/sn127/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
