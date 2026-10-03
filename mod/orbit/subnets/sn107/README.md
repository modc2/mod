# sn107 — Minos ミ

The Foundational Layer of Genomics

Bittensor subnet **107** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-03 (block 9200279).

Links: [github](https://github.com/minos-protocol/minos_subnet) · [url](https://theminos.ai) · [discord](https://discord.com/channels/799672011265015819/1467949024769478793)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.040465 | -0.11% | +0.12% | -2.17% | 89,366 | 20,852 | 1,099 |

## Last 24h flow

133 trades by 82 coldkeys · 55 buys (441.77 τ) / 78 sells (538.97 τ) · net -97.21 τ

## News

- 2026-09-04 · release · [v0.3.0: Minos 🧬 — Difficulty-weighted scoring (v2), round verification, config commitments](https://github.com/minos-protocol/minos_subnet/releases/tag/v0.3.0) — minos-protocol/minos_subnet
- 2026-09-04 · commit · [Merge pull request #39 from minos-protocol/feat/round-verification](https://github.com/minos-protocol/minos_subnet/commit/289e54ae4cc913be93f5dfe8c44febb29363dc7b) — minos-protocol/minos_subnet
- 2026-09-03 · commit · [Add round verification docs and a round-check script](https://github.com/minos-protocol/minos_subnet/commit/dc0d279ebc952b1d0edae1555932f858af09b932) — minos-protocol/minos_subnet

## Use

```bash
m subnets.sn107/info        # live identity + market (snapshot if bt is down)
m subnets.sn107/news        # scraped news
m subnets.sn107/trades      # 24h alpha tape
m subnets.sn107/daily       # daily candles
python3 orbit/subnets/sn107/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
