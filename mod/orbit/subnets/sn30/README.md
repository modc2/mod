# sn30 — Endure Network ו

The risk intelligence network

Bittensor subnet **30** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-20 (block 9322663).

Links: [github](https://github.com/Endure-Network/endure-subnet) · [url](https://endure.network/) · [discord](https://discord.gg/SqT3X7T8zv)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003301 | +0.68% | +0.12% | -0.16% | 20,401 | 8,380 | 210.24 |

## Last 24h flow

70 trades by 16 coldkeys · 7 buys (107.76 τ) / 63 sells (101.34 τ) · net 6.41 τ

## News

- 2026-10-05 · release · [v0.1.2 — Owner-controlled burn, protocol 2043](https://github.com/endure-network/endure-subnet/releases/tag/v0.1.2) — Endure-Network/endure-subnet
- 2026-10-05 · commit · [Merge pull request #81 from endure-network/staging](https://github.com/endure-network/endure-subnet/commit/4376ff42a6b619ed995144b63371ec2e82125316) — Endure-Network/endure-subnet
- 2026-10-05 · commit · [Merge pull request #80 from endure-network/develop](https://github.com/endure-network/endure-subnet/commit/cd847c4bff4052543a22957dade4470786e3ea66) — Endure-Network/endure-subnet
- 2026-10-05 · commit · [release: prepare v0.1.2 metadata and record the release decision (#79)](https://github.com/endure-network/endure-subnet/commit/672317e4a0935f4de889c363da654fa08f577dd4) — Endure-Network/endure-subnet
- 2026-10-03 · commit · [Merge pull request #78 from endure-network/develop](https://github.com/endure-network/endure-subnet/commit/02ab118662e910d14077c8f6cd491fb60d7c2d24) — Endure-Network/endure-subnet
- 2026-10-02 · commit · [feat(protocol): owner-published burn rate for scored votes (2043) (#77)](https://github.com/endure-network/endure-subnet/commit/68603ab6156dd9711a653a1f3403fe573003c8a5) — Endure-Network/endure-subnet
- 2026-09-28 · release · [v0.1.1 — Alpha Risk, protocol 2042](https://github.com/endure-network/endure-subnet/releases/tag/v0.1.1) — Endure-Network/endure-subnet
- 2026-09-20 · release · [v0.1.0 — Alpha Risk](https://github.com/endure-network/endure-subnet/releases/tag/v0.1.0) — Endure-Network/endure-subnet

## Use

```bash
m subnets.sn30/info        # live identity + market (snapshot if bt is down)
m subnets.sn30/news        # scraped news
m subnets.sn30/trades      # 24h alpha tape
m subnets.sn30/daily       # daily candles
python3 orbit/subnets/sn30/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
