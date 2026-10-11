# sn63 — Enigma س

Breaking today to build tomorrow

Bittensor subnet **63** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-11 (block 9257875).

Links: [github](https://github.com/qbittensor-labs/enigma) · [url](https://www.qbittensorlabs.com/) · discord `qbittensorlabs`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003945 | -0.00% | -1.34% | -0.48% | 24,269 | 10,777 | 89.63 |

## Last 24h flow

21 trades by 20 coldkeys · 9 buys (8.30 τ) / 12 sells (81.02 τ) · net -72.72 τ

## News

- 2026-09-16 · commit · [\[create-pull-request\] automated change (#44)](https://github.com/qbittensor-labs/enigma/commit/feafea5a481712228268b70db54ab31f304b7e89) — qbittensor-labs/enigma
- 2026-09-14 · commit · [\[create-pull-request\] automated change (#43)](https://github.com/qbittensor-labs/enigma/commit/2d5efee5460e3a68493cca0019964e78114324ee) — qbittensor-labs/enigma
- 2026-09-11 · commit · [Add initial burn sync code and adjust when weights are set](https://github.com/qbittensor-labs/enigma/commit/b1fbd3548149400fab530cc034bbc5eccde1fac3) — qbittensor-labs/enigma

## Use

```bash
m subnets.sn63/info        # live identity + market (snapshot if bt is down)
m subnets.sn63/news        # scraped news
m subnets.sn63/trades      # 24h alpha tape
m subnets.sn63/daily       # daily candles
python3 orbit/subnets/sn63/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
