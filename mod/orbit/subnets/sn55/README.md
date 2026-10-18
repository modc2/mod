# sn55 — NIOME ث

NIOME is a decentralized AI subnet that enables privacy-safe genomic intelligence by replacing real human genomes with high-fidelity synthetic genomic profiles

Bittensor subnet **55** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-18 (block 9308119).

Links: [github](https://github.com/genomesio/subnet-niome) · [url](https://niome.genomes.io) · [discord](https://discord.gg/7mJkaJZX)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002096 | +0.00% | -1.07% | -4.93% | 12,009 | 5,818 | 172.63 |

## Last 24h flow

45 trades by 23 coldkeys · 13 buys (70.60 τ) / 32 sells (101.71 τ) · net -31.11 τ

## News

- 2026-09-23 · commit · [use block hashes to generate seeds](https://github.com/genomesio/subnet-niome/commit/9d9347a7ffab85a04eda6c36b9e87c59c8bb4049) — genomesio/subnet-niome

## Use

```bash
m subnets.sn55/info        # live identity + market (snapshot if bt is down)
m subnets.sn55/news        # scraped news
m subnets.sn55/trades      # 24h alpha tape
m subnets.sn55/daily       # daily candles
python3 orbit/subnets/sn55/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
