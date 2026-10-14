# sn63 — Enigma س

Breaking today to build tomorrow

Bittensor subnet **63** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-14 (block 9279460).

Links: [github](https://github.com/qbittensor-labs/enigma) · [url](https://www.qbittensorlabs.com/) · discord `qbittensorlabs`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003896 | -0.03% | -3.30% | -1.50% | 24,062 | 10,717 | 617.68 |

## Last 24h flow

54 trades by 33 coldkeys · 22 buys (218.37 τ) / 32 sells (398.50 τ) · net -180.13 τ

## News

- 2026-10-13 · commit · [Track whether a terminal verify update reached the platform and HQP L…](https://github.com/qbittensor-labs/enigma/commit/9860c5c90e06359c5b17944ba9c6992b87e11f31) — qbittensor-labs/enigma
- 2026-09-16 · commit · [\[create-pull-request\] automated change (#44)](https://github.com/qbittensor-labs/enigma/commit/feafea5a481712228268b70db54ab31f304b7e89) — qbittensor-labs/enigma
- 2026-09-14 · commit · [\[create-pull-request\] automated change (#43)](https://github.com/qbittensor-labs/enigma/commit/2d5efee5460e3a68493cca0019964e78114324ee) — qbittensor-labs/enigma

## Use

```bash
m subnets.sn63/info        # live identity + market (snapshot if bt is down)
m subnets.sn63/news        # scraped news
m subnets.sn63/trades      # 24h alpha tape
m subnets.sn63/daily       # daily candles
python3 orbit/subnets/sn63/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
