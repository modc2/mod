# sn63 — Enigma س

Breaking today to build tomorrow

Bittensor subnet **63** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-19 (block 9314686).

Links: [github](https://github.com/qbittensor-labs/enigma) · [url](https://www.qbittensorlabs.com/) · discord `qbittensorlabs`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003812 | — | -0.00% | -4.90% | 23,687 | 10,610 | 40.46 |

## Last 24h flow

8 trades by 5 coldkeys · 4 buys (20.11 τ) / 4 sells (20.03 τ) · net 0.08 τ

## News

- 2026-10-14 · commit · [Record submission id when requesting metadata for traceability](https://github.com/qbittensor-labs/enigma/commit/e3eb850e9748142d028618bcda2c4878f06fd990) — qbittensor-labs/enigma
- 2026-10-13 · commit · [Track whether a terminal verify update reached the platform and HQP L…](https://github.com/qbittensor-labs/enigma/commit/9860c5c90e06359c5b17944ba9c6992b87e11f31) — qbittensor-labs/enigma

## Use

```bash
m subnets.sn63/info        # live identity + market (snapshot if bt is down)
m subnets.sn63/news        # scraped news
m subnets.sn63/trades      # 24h alpha tape
m subnets.sn63/daily       # daily candles
python3 orbit/subnets/sn63/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
