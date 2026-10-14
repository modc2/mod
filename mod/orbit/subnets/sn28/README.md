# sn28 — SayGM ד

Drop-in access to Claude, GPT, and Gemini.

Bittensor subnet **28** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-14 (block 9279460).

Links: [github](https://github.com/taostat/gm-miner) · [url](https://saygm.com/)

Fleet mods for this subnet: `saygm`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.030351 | -1.26% | +2.61% | +33.29% | 191,789 | 27,433 | 10,667 |

## Last 24h flow

902 trades by 359 coldkeys · 451 buys (5,452 τ) / 451 sells (5,138 τ) · net 314.74 τ

## News

- 2026-10-12 · release · [v0.4.28](https://github.com/taostat/gm-miner/releases/tag/v0.4.28) — taostat/gm-miner
- 2026-10-12 · commit · [chore(release): promote gm-miner 0.4.28 (#317)](https://github.com/taostat/gm-miner/commit/025fcb1077f0d363f9b56dd8097c79f957b2073a) — taostat/gm-miner
- 2026-10-12 · release · [v0.4.28-dev](https://github.com/taostat/gm-miner/releases/tag/v0.4.28-dev) — taostat/gm-miner
- 2026-10-12 · commit · [chore(release): gm-miner 0.4.28-dev (#316)](https://github.com/taostat/gm-miner/commit/5ff22f12b72767f2e96a479f7deadbf5f70dbb58) — taostat/gm-miner
- 2026-10-12 · commit · [gmcli: parse Envoy templates with serde-saphyr (#315)](https://github.com/taostat/gm-miner/commit/ed77456c2252211c122c2d25673f9c4749476f05) — taostat/gm-miner
- 2026-10-12 · commit · [gmcli: accept only registry-attested instance types (#314)](https://github.com/taostat/gm-miner/commit/115b7ac717ddb3adbb7993fc335001ea79211ceb) — taostat/gm-miner
- 2026-10-12 · release · [v0.4.27](https://github.com/taostat/gm-miner/releases/tag/v0.4.27) — taostat/gm-miner
- 2026-10-12 · commit · [chore(release): promote gm-miner 0.4.27 (#313)](https://github.com/taostat/gm-miner/commit/30220e7f3fcb5ca12beeae05212dddc0cdb1e239) — taostat/gm-miner

## Use

```bash
m subnets.sn28/info        # live identity + market (snapshot if bt is down)
m subnets.sn28/news        # scraped news
m subnets.sn28/trades      # 24h alpha tape
m subnets.sn28/daily       # daily candles
python3 orbit/subnets/sn28/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
