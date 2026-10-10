# sn63 — Enigma س

Breaking today to build tomorrow

Bittensor subnet **63** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-10 (block 9250674).

Links: [github](https://github.com/qbittensor-labs/enigma) · [url](https://www.qbittensorlabs.com/) · discord `qbittensorlabs`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003998 | +0.00% | +1.23% | -0.02% | 24,569 | 10,848 | 546.91 |

## Last 24h flow

49 trades by 29 coldkeys · 23 buys (306.68 τ) / 26 sells (239.75 τ) · net 66.92 τ

## News

- 2026-09-16 · commit · [[create-pull-request] automated change (#44)](https://github.com/qbittensor-labs/enigma/commit/feafea5a481712228268b70db54ab31f304b7e89) — qbittensor-labs/enigma
- 2026-09-14 · commit · [[create-pull-request] automated change (#43)](https://github.com/qbittensor-labs/enigma/commit/2d5efee5460e3a68493cca0019964e78114324ee) — qbittensor-labs/enigma
- 2026-09-11 · commit · [Add initial burn sync code and adjust when weights are set](https://github.com/qbittensor-labs/enigma/commit/b1fbd3548149400fab530cc034bbc5eccde1fac3) — qbittensor-labs/enigma
- 2026-09-10 · commit · [Fix issue with migrated validator db](https://github.com/qbittensor-labs/enigma/commit/6863ee28db97b7f6591a51f7d8e2cc9b752f09a9) — qbittensor-labs/enigma

## Use

```bash
m subnets.sn63/info        # live identity + market (snapshot if bt is down)
m subnets.sn63/news        # scraped news
m subnets.sn63/trades      # 24h alpha tape
m subnets.sn63/daily       # daily candles
python3 orbit/subnets/sn63/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
