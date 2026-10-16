# sn28 — SayGM ד

Drop-in access to Claude, GPT, and Gemini.

Bittensor subnet **28** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-16 (block 9293868).

Links: [github](https://github.com/taostat/gm-miner) · [url](https://saygm.com/)

Fleet mods for this subnet: `saygm`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.032922 | +0.91% | -1.30% | +36.49% | 208,567 | 28,634 | 16,565 |

## Last 24h flow

1050 trades by 334 coldkeys · 521 buys (8,052 τ) / 529 sells (8,356 τ) · net -304.41 τ

## News

- 2026-10-15 · release · [v0.5.1-dev](https://github.com/taostat/gm-miner/releases/tag/v0.5.1-dev) — taostat/gm-miner
- 2026-10-15 · commit · [Add Engy MiniMax H3 video route (#324)](https://github.com/taostat/gm-miner/commit/70bc539e8bcbcfd21cf09ee252af2e2b1b757f4b) — taostat/gm-miner
- 2026-10-15 · commit · [Document DeepInfra GLM-5.3 and Flash sourcing (#323)](https://github.com/taostat/gm-miner/commit/3de24065faf3357e2116529677f4dcb0c6c2b402) — taostat/gm-miner
- 2026-10-14 · release · [v0.5.0](https://github.com/taostat/gm-miner/releases/tag/v0.5.0) — taostat/gm-miner
- 2026-10-14 · commit · [chore(release): promote gm-miner 0.5.0 (#322)](https://github.com/taostat/gm-miner/commit/9324860559305fb65993490c624f4b390aeafc0c) — taostat/gm-miner
- 2026-10-14 · release · [v0.4.29-dev](https://github.com/taostat/gm-miner/releases/tag/v0.4.29-dev) — taostat/gm-miner
- 2026-10-14 · commit · [chore(release): gm-miner 0.4.29-dev (#320)](https://github.com/taostat/gm-miner/commit/e1f325c20c301b5fe9ea48a6e6238a25a488a2ff) — taostat/gm-miner
- 2026-10-14 · commit · [Pin dstack-0.5.12 as the deploy OS image (#318)](https://github.com/taostat/gm-miner/commit/5b895ddb560cfa79271f15e4fc117930e9f60a4e) — taostat/gm-miner

## Use

```bash
m subnets.sn28/info        # live identity + market (snapshot if bt is down)
m subnets.sn28/news        # scraped news
m subnets.sn28/trades      # 24h alpha tape
m subnets.sn28/daily       # daily candles
python3 orbit/subnets/sn28/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
