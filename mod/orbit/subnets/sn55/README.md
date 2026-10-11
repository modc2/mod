# sn55 — NIOME ث

NIOME is a decentralized AI subnet that enables privacy-safe genomic intelligence by replacing real human genomes with high-fidelity synthetic genomic profiles

Bittensor subnet **55** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-11 (block 9257875).

Links: [github](https://github.com/genomesio/subnet-niome) · [url](https://niome.genomes.io) · [discord](https://discord.gg/7mJkaJZX)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002205 | -0.05% | -7.73% | -9.26% | 12,521 | 5,966 | 1,438 |

## Last 24h flow

238 trades by 93 coldkeys · 73 buys (596.37 τ) / 165 sells (840.57 τ) · net -244.20 τ

## News

- 2026-09-23 · commit · [use block hashes to generate seeds](https://github.com/genomesio/subnet-niome/commit/9d9347a7ffab85a04eda6c36b9e87c59c8bb4049) — genomesio/subnet-niome
- 2026-09-11 · commit · [upload all miners submissions](https://github.com/genomesio/subnet-niome/commit/9f3ada447b8fbec8d5b200694507f134ea8b9a6b) — genomesio/subnet-niome

## Use

```bash
m subnets.sn55/info        # live identity + market (snapshot if bt is down)
m subnets.sn55/news        # scraped news
m subnets.sn55/trades      # 24h alpha tape
m subnets.sn55/daily       # daily candles
python3 orbit/subnets/sn55/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
