# sn28 — SayGM ד

Drop-in access to Claude, GPT, and Gemini.

Bittensor subnet **28** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-05 (block 9214666).

Links: [github](https://github.com/taostat/gm-miner) · [url](https://saygm.com/)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.021027 | +0.26% | -2.39% | +2.20% | 131,325 | 22,635 | 4,222 |

## Last 24h flow

294 trades by 150 coldkeys · 142 buys (1,927 τ) / 152 sells (2,235 τ) · net -308.34 τ

## News

- 2026-10-04 · release · [v0.4.25](https://github.com/taostat/gm-miner/releases/tag/v0.4.25) — taostat/gm-miner
- 2026-10-04 · commit · [Handle scheduled miner price increases as successful declarations (#293)](https://github.com/taostat/gm-miner/commit/6ae50a4253e7d9ff46c03af325d3664f9baea332) — taostat/gm-miner
- 2026-09-28 · release · [v0.4.24](https://github.com/taostat/gm-miner/releases/tag/v0.4.24) — taostat/gm-miner
- 2026-09-28 · commit · [chore(release): promote gm-miner 0.4.24 (#291)](https://github.com/taostat/gm-miner/commit/163a5c676c3a65558694a36f6b3bc8bbe59f27d8) — taostat/gm-miner
- 2026-09-28 · release · [v0.4.24-dev](https://github.com/taostat/gm-miner/releases/tag/v0.4.24-dev) — taostat/gm-miner
- 2026-09-27 · commit · [chore(release): gm-miner 0.4.24-dev (#290)](https://github.com/taostat/gm-miner/commit/ee94e78f819c795f75092bbf740d4cc15ef018da) — taostat/gm-miner
- 2026-09-27 · commit · [test(attestd): forward supplier trailers through the KubeTEE verifier…](https://github.com/taostat/gm-miner/commit/3bd8db58e8199358add52dcd112283e1cd270d22) — taostat/gm-miner
- 2026-09-27 · commit · [Serve KubeTEE chat on attested upstream connections (#286)](https://github.com/taostat/gm-miner/commit/88a771dcd99bb4357bfbce9a31ac1d1728e8e417) — taostat/gm-miner

## Use

```bash
m subnets.sn28/info        # live identity + market (snapshot if bt is down)
m subnets.sn28/news        # scraped news
m subnets.sn28/trades      # 24h alpha tape
m subnets.sn28/daily       # daily candles
python3 orbit/subnets/sn28/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
