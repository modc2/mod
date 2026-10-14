# sn93 — Bitcast ᚃ

The Decentralized Creators Economy

Bittensor subnet **93** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-14 (block 9279460).

Links: [github](https://github.com/bitcast-network/bitcast) · [url](https://stats.bitcast.network/)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.018904 | -1.30% | -1.13% | -2.01% | 100,693 | 24,270 | 5,140 |

## Last 24h flow

350 trades by 140 coldkeys · 147 buys (2,417 τ) / 203 sells (2,628 τ) · net -211.58 τ

## News

- 2026-09-17 · commit · [docs: replace CLAUDE.md with AGENTS.md + on-chain liveness verificati…](https://github.com/bitcast-network/bitcast/commit/e27397ed0a10402d0a14e6ae8a327802fcee8c43) — bitcast-network/bitcast
- 2026-09-17 · commit · [chore: remove subnet identity logo from repo (#175)](https://github.com/bitcast-network/bitcast/commit/b3e6f81761e0de1edac55dead5d8f83a50c40021) — bitcast-network/bitcast

## Use

```bash
m subnets.sn93/info        # live identity + market (snapshot if bt is down)
m subnets.sn93/news        # scraped news
m subnets.sn93/trades      # 24h alpha tape
m subnets.sn93/daily       # daily candles
python3 orbit/subnets/sn93/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
