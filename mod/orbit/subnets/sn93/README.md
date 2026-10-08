# sn93 — Bitcast ᚃ

The Decentralized Creators Economy

Bittensor subnet **93** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-08 (block 9236282).

Links: [github](https://github.com/bitcast-network/bitcast) · [url](https://stats.bitcast.network/)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.018815 | -0.14% | -2.47% | -2.31% | 99,278 | 24,085 | 2,899 |

## Last 24h flow

307 trades by 159 coldkeys · 107 buys (1,236 τ) / 200 sells (1,594 τ) · net -358.31 τ

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
