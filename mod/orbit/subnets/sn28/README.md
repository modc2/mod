# sn28 — SayGM ד

Drop-in access to Claude, GPT, and Gemini.

Bittensor subnet **28** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-07 (block 9229089).

Links: [github](https://github.com/taostat/gm-miner) · [url](https://saygm.com/)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.022771 | +0.69% | +4.46% | +9.84% | 142,586 | 23,598 | 5,239 |

## Last 24h flow

496 trades by 240 coldkeys · 265 buys (2,800 τ) / 231 sells (2,356 τ) · net 444.15 τ

## News

- 2026-10-06 · commit · [Remove paid Gemini image-canary command (#299)](https://github.com/taostat/gm-miner/commit/556de337c0a9f97998a9ba1fb7610e2bedf0fcbe) — taostat/gm-miner
- 2026-10-06 · release · [v0.4.26](https://github.com/taostat/gm-miner/releases/tag/v0.4.26) — taostat/gm-miner
- 2026-10-06 · commit · [Add gmcli notifications list, set, confirm, status and off (#297)](https://github.com/taostat/gm-miner/commit/deab605b3bb752aed1bb074a6e248ee3a77f46bd) — taostat/gm-miner
- 2026-10-05 · commit · [fix(cli): retry rate-limited token requests instead of re-logging in …](https://github.com/taostat/gm-miner/commit/4eb759e1326ad1f15e84dfd37542035865ca0b02) — taostat/gm-miner
- 2026-10-04 · release · [v0.4.25](https://github.com/taostat/gm-miner/releases/tag/v0.4.25) — taostat/gm-miner
- 2026-10-04 · commit · [Handle scheduled miner price increases as successful declarations (#293)](https://github.com/taostat/gm-miner/commit/6ae50a4253e7d9ff46c03af325d3664f9baea332) — taostat/gm-miner
- 2026-09-28 · release · [v0.4.24](https://github.com/taostat/gm-miner/releases/tag/v0.4.24) — taostat/gm-miner
- 2026-09-28 · commit · [chore(release): promote gm-miner 0.4.24 (#291)](https://github.com/taostat/gm-miner/commit/163a5c676c3a65558694a36f6b3bc8bbe59f27d8) — taostat/gm-miner

## Use

```bash
m subnets.sn28/info        # live identity + market (snapshot if bt is down)
m subnets.sn28/news        # scraped news
m subnets.sn28/trades      # 24h alpha tape
m subnets.sn28/daily       # daily candles
python3 orbit/subnets/sn28/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
