# sn55 — NIOME ث

NIOME is a decentralized AI subnet that enables privacy-safe genomic intelligence by replacing real human genomes with high-fidelity synthetic genomic profiles

Bittensor subnet **55** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-10 (block 9250674).

Links: [github](https://github.com/genomesio/subnet-niome) · [url](https://niome.genomes.io) · [discord](https://discord.gg/7mJkaJZX)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002389 | -0.49% | +18.38% | +4.51% | 13,553 | 6,211 | 6,420 |

## Last 24h flow

681 trades by 162 coldkeys · 347 buys (3,476 τ) / 334 sells (2,956 τ) · net 520.09 τ

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
