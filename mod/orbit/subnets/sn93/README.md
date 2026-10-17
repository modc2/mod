# sn93 — Bitcast ᚃ

The Decentralized Creators Economy

Bittensor subnet **93** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-17 (block 9300804).

Links: [github](https://github.com/bitcast-network/bitcast) · [url](https://stats.bitcast.network/)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.020036 | -0.56% | +1.70% | +4.41% | 107,214 | 25,052 | 6,300 |

## Last 24h flow

339 trades by 156 coldkeys · 159 buys (3,115 τ) / 180 sells (3,031 τ) · net 84.29 τ

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
