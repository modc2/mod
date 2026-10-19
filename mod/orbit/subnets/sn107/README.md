# sn107 — Minos ミ

The Foundational Layer of Genomics

Bittensor subnet **107** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-19 (block 9314686).

Links: [github](https://github.com/minos-protocol/minos_subnet) · [url](https://theminos.ai) · [discord](https://discord.com/channels/799672011265015819/1467949024769478793)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.032424 | — | -3.89% | -12.49% | 76,416 | 19,865 | 1,622 |

## Last 24h flow

170 trades by 101 coldkeys · 46 buys (534.10 τ) / 124 sells (1,004 τ) · net -470.05 τ

## News

- 2026-10-09 · commit · [Merge pull request #40 from minos-protocol/fix/scoring-cutoff-lead](https://github.com/minos-protocol/minos_subnet/commit/4b3c0944b517d09be9b54eb8fa3158ae700b5d5f) — minos-protocol/minos_subnet
- 2026-10-09 · commit · [Bound the scoring phase by the submit window](https://github.com/minos-protocol/minos_subnet/commit/30465ca777356e3990b419b5b9f86863f96f2109) — minos-protocol/minos_subnet

## Use

```bash
m subnets.sn107/info        # live identity + market (snapshot if bt is down)
m subnets.sn107/news        # scraped news
m subnets.sn107/trades      # 24h alpha tape
m subnets.sn107/daily       # daily candles
python3 orbit/subnets/sn107/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
