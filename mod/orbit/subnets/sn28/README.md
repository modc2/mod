# sn28 — SayGM ד

Drop-in access to Claude, GPT, and Gemini.

Bittensor subnet **28** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-15 (block 9286668).

Links: [github](https://github.com/taostat/gm-miner) · [url](https://saygm.com/)

Fleet mods for this subnet: `saygm`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.033340 | +0.50% | +9.49% | +41.13% | 210,948 | 28,783 | 11,944 |

## Last 24h flow

957 trades by 346 coldkeys · 566 buys (6,506 τ) / 391 sells (5,325 τ) · net 1,182 τ

## News

- 2026-10-14 · release · [v0.5.0](https://github.com/taostat/gm-miner/releases/tag/v0.5.0) — taostat/gm-miner
- 2026-10-14 · commit · [chore(release): promote gm-miner 0.5.0 (#322)](https://github.com/taostat/gm-miner/commit/9324860559305fb65993490c624f4b390aeafc0c) — taostat/gm-miner
- 2026-10-14 · release · [v0.4.29-dev](https://github.com/taostat/gm-miner/releases/tag/v0.4.29-dev) — taostat/gm-miner
- 2026-10-14 · commit · [chore(release): gm-miner 0.4.29-dev (#320)](https://github.com/taostat/gm-miner/commit/e1f325c20c301b5fe9ea48a6e6238a25a488a2ff) — taostat/gm-miner
- 2026-10-14 · commit · [Pin dstack-0.5.12 as the deploy OS image (#318)](https://github.com/taostat/gm-miner/commit/5b895ddb560cfa79271f15e4fc117930e9f60a4e) — taostat/gm-miner
- 2026-10-14 · commit · [Update rustls to 0.23.46 (#319)](https://github.com/taostat/gm-miner/commit/bcabe40d7f5fb47a0a74a033b8f7f80557cb729c) — taostat/gm-miner
- 2026-10-12 · release · [v0.4.28](https://github.com/taostat/gm-miner/releases/tag/v0.4.28) — taostat/gm-miner
- 2026-10-12 · commit · [chore(release): promote gm-miner 0.4.28 (#317)](https://github.com/taostat/gm-miner/commit/025fcb1077f0d363f9b56dd8097c79f957b2073a) — taostat/gm-miner

## Use

```bash
m subnets.sn28/info        # live identity + market (snapshot if bt is down)
m subnets.sn28/news        # scraped news
m subnets.sn28/trades      # 24h alpha tape
m subnets.sn28/daily       # daily candles
python3 orbit/subnets/sn28/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
