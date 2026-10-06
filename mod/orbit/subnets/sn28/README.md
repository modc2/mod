# sn28 — SayGM ד

Drop-in access to Claude, GPT, and Gemini.

Bittensor subnet **28** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-06 (block 9221879).

Links: [github](https://github.com/taostat/gm-miner) · [url](https://saygm.com/)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.021798 | +0.02% | +3.66% | +3.32% | 136,314 | 23,067 | 3,805 |

## Last 24h flow

335 trades by 162 coldkeys · 117 buys (2,058 τ) / 218 sells (1,681 τ) · net 376.32 τ

## News

- 2026-10-05 · commit · [fix(cli): retry rate-limited token requests instead of re-logging in …](https://github.com/taostat/gm-miner/commit/4eb759e1326ad1f15e84dfd37542035865ca0b02) — taostat/gm-miner
- 2026-10-04 · release · [v0.4.25](https://github.com/taostat/gm-miner/releases/tag/v0.4.25) — taostat/gm-miner
- 2026-10-04 · commit · [Handle scheduled miner price increases as successful declarations (#293)](https://github.com/taostat/gm-miner/commit/6ae50a4253e7d9ff46c03af325d3664f9baea332) — taostat/gm-miner
- 2026-09-28 · release · [v0.4.24](https://github.com/taostat/gm-miner/releases/tag/v0.4.24) — taostat/gm-miner
- 2026-09-28 · commit · [chore(release): promote gm-miner 0.4.24 (#291)](https://github.com/taostat/gm-miner/commit/163a5c676c3a65558694a36f6b3bc8bbe59f27d8) — taostat/gm-miner
- 2026-09-28 · release · [v0.4.24-dev](https://github.com/taostat/gm-miner/releases/tag/v0.4.24-dev) — taostat/gm-miner
- 2026-09-27 · commit · [chore(release): gm-miner 0.4.24-dev (#290)](https://github.com/taostat/gm-miner/commit/ee94e78f819c795f75092bbf740d4cc15ef018da) — taostat/gm-miner
- 2026-09-27 · commit · [test(attestd): forward supplier trailers through the KubeTEE verifier…](https://github.com/taostat/gm-miner/commit/3bd8db58e8199358add52dcd112283e1cd270d22) — taostat/gm-miner

## Use

```bash
m subnets.sn28/info        # live identity + market (snapshot if bt is down)
m subnets.sn28/news        # scraped news
m subnets.sn28/trades      # 24h alpha tape
m subnets.sn28/daily       # daily candles
python3 orbit/subnets/sn28/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
